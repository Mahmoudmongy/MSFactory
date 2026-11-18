/* MS-Factory frontend.
 *
 * Single-page, hash-routed, no build step: the whole app is one ES module so it
 * can be served straight from /static and installed as a PWA. Structure:
 *   store    — tokens, session, cached lookups
 *   api      — fetch wrapper with auth, refresh-on-401 and error normalisation
 *   ui       — tiny DOM helpers (el, fmt, toast, sheet)
 *   screens  — one render function per route
 */

// ---------------------------------------------------------------- store

const STORAGE_KEY = "msfactory.session";

const store = {
  session: null,
  user: null,
  memberships: [],
  companyId: null,
  cache: { accounts: null, contacts: null, settings: null },

  load() {
    try {
      this.session = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    } catch {
      this.session = null;
    }
    return this.session;
  },

  save(session) {
    this.session = session;
    if (session) localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    else localStorage.removeItem(STORAGE_KEY);
  },

  get accessToken() {
    return this.session?.access_token || null;
  },

  get company() {
    return this.memberships.find((m) => m.company.id === this.companyId)?.company || null;
  },

  get role() {
    return this.memberships.find((m) => m.company.id === this.companyId)?.role || "";
  },

  can(action) {
    const role = this.role;
    if (role === "owner" || role === "admin") return true;
    if (role === "accountant") return !["company.manage", "member.manage"].includes(action);
    if (role === "viewer") return action.endsWith(".view") || action === "report.view";
    return false;
  },

  reset() {
    this.user = null;
    this.memberships = [];
    this.companyId = null;
    this.cache = { accounts: null, contacts: null };
    this.save(null);
  },
};

// ---------------------------------------------------------------- api

class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

let refreshing = null;

async function api(path, { method = "GET", body, headers = {}, raw = false } = {}) {
  const requestHeaders = { ...headers };
  if (body !== undefined) requestHeaders["Content-Type"] = "application/json";
  if (store.accessToken) requestHeaders.Authorization = `Bearer ${store.accessToken}`;
  if (store.companyId) requestHeaders["X-Company-Id"] = String(store.companyId);

  const response = await fetch(`/api/v1${path}`, {
    method,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  // A single in-flight refresh is shared by all concurrent 401s.
  if (response.status === 401 && store.session?.refresh_token && !path.startsWith("/auth/")) {
    refreshing =
      refreshing ||
      fetch("/api/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: store.session.refresh_token }),
      })
        .then(async (res) => {
          if (!res.ok) throw new ApiError("انتهت الجلسة، من فضلك سجّل الدخول مرة أخرى", 401, "session_expired");
          store.save(await res.json());
          return true;
        })
        .finally(() => {
          refreshing = null;
        });

    await refreshing;
    return api(path, { method, body, headers, raw });
  }

  if (response.status === 204) return null;

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const message =
      (typeof detail === "string" && detail) ||
      detail?.message ||
      (Array.isArray(detail) && detail[0]?.msg) ||
      "حدث خطأ غير متوقع";
    const code = detail?.code || (Array.isArray(detail) ? "validation_error" : "error");
    throw new ApiError(message, response.status, code);
  }
  return raw ? response : payload;
}

/** Deterministic idempotency key so a retried submit cannot double-post. */
function idempotencyKey(prefix, payload) {
  const seed = `${prefix}:${store.companyId}:${JSON.stringify(payload)}`;
  let hash = 0;
  for (let i = 0; i < seed.length; i++) hash = (hash * 31 + seed.charCodeAt(i)) | 0;
  return `${prefix}-${Math.abs(hash).toString(36)}`;
}

// ---------------------------------------------------------------- ui helpers

export const el = (html) => {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return template.content.firstElementChild;
};

export const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);

const CURRENCY = { EGP: "ج.م", SAR: "ر.س", AED: "د.إ", USD: "$", EUR: "€" };

export function money(value, currency) {
  const amount = Number(value ?? 0);
  const formatted = new Intl.NumberFormat("ar-EG", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Math.abs(amount));
  const symbol = CURRENCY[currency || store.company?.base_currency || "EGP"] || "";
  return `${amount < 0 ? "-" : ""}${formatted} ${symbol}`.trim();
}

export function date(value) {
  if (!value) return "—";
  const parsed = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat("ar-EG", { day: "numeric", month: "short", year: "numeric" }).format(parsed);
}

export function today() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

export function toast(message, kind = "") {
  const node = document.getElementById("toast");
  node.textContent = message;
  node.className = `toast toast--show ${kind ? `toast--${kind}` : ""}`;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => {
    node.className = "toast";
  }, kind === "error" ? 4200 : 2600);
}

const STATUS_LABELS = {
  draft: "مسودة",
  issued: "صادرة",
  sent: "مرسلة",
  paid: "مدفوعة",
  partially_paid: "مدفوعة جزئياً",
  void: "ملغاة",
  overdue: "متأخرة",
};

export const statusLabel = (status) => STATUS_LABELS[status] || status;
export const statusBadge = (status) =>
  `<span class="badge badge--${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>`;

export function spinner() {
  return '<div class="spinner" role="status" aria-label="جارٍ التحميل"></div>';
}

export function emptyState(icon, title, text) {
  return `<div class="empty"><span class="empty__icon">${icon}</span>
    <div class="empty__title">${escapeHtml(title)}</div>
    <div class="empty__text">${escapeHtml(text)}</div></div>`;
}

/** Bottom sheet used for create/edit forms on mobile. */
export function openSheet(title, contentNode) {
  const backdrop = el(`<div class="sheet-backdrop"><div class="sheet">
      <div class="sheet__handle"></div>
      <h2 class="sheet__title">${escapeHtml(title)}</h2>
    </div></div>`);
  const sheet = backdrop.querySelector(".sheet");
  sheet.appendChild(contentNode);
  const close = () => backdrop.remove();
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) close();
  });
  document.body.appendChild(backdrop);
  sheet.scrollIntoView({ block: "end" });
  return close;
}

export function confirmDialog(message) {
  return new Promise((resolve) => {
    const backdrop = el(`<div class="sheet-backdrop"><div class="sheet">
        <div class="sheet__handle"></div>
        <p style="margin:4px 0 20px;font-weight:600">${escapeHtml(message)}</p>
        <div style="display:flex;gap:10px">
          <button class="btn btn--ghost" style="flex:1" data-no>إلغاء</button>
          <button class="btn btn--danger" style="flex:1" data-yes>تأكيد</button>
        </div>
      </div></div>`);
    backdrop.querySelector("[data-no]").onclick = () => {
      backdrop.remove();
      resolve(false);
    };
    backdrop.querySelector("[data-yes]").onclick = () => {
      backdrop.remove();
      resolve(true);
    };
    document.body.appendChild(backdrop);
  });
}

// ---------------------------------------------------------------- app shell

const app = document.getElementById("app");

function setScreen(content, { appbar, tabbar, plain } = {}) {
  app.innerHTML = "";
  if (appbar) app.appendChild(appbar);
  const screen = el(`<main class="screen${plain ? " screen--plain" : ""}"></main>`);
  if (typeof content === "string") screen.innerHTML = content;
  else screen.appendChild(content);
  app.appendChild(screen);
  if (tabbar) app.appendChild(tabbar);
}

function appbar({ title, subtitle, back, actions = "" }) {
  return el(`<header class="appbar">
    ${back ? `<button class="icon-btn" data-nav="${escapeHtml(back)}" aria-label="رجوع">→</button>` : ""}
    <h1 class="appbar__title">${escapeHtml(title)}
      ${subtitle ? `<span class="appbar__sub">${escapeHtml(subtitle)}</span>` : ""}</h1>
    ${actions}
  </header>`);
}

const TABS = [
  { href: "#/dashboard", icon: "🏠", label: "الرئيسية" },
  { href: "#/invoices", icon: "🧾", label: "الفواتير" },
  { href: "#/payments", icon: "💵", label: "المدفوعات" },
  { href: "#/contacts", icon: "👥", label: "العملاء" },
  { href: "#/reports", icon: "📊", label: "التقارير" },
];

function tabbar() {
  const current = location.hash || "#/dashboard";
  return el(`<nav class="tabbar">${TABS.map(
    (tab) => `<a class="tabbar__item" href="${tab.href}" ${current.startsWith(tab.href) ? 'aria-current="page"' : ""}>
        <span class="tabbar__icon">${tab.icon}</span><span>${tab.label}</span></a>`
  ).join("")}</nav>`);
}

// ---------------------------------------------------------------- auth screens

function renderAuth(mode = "login") {
  const isLogin = mode === "login";
  const view = el(`<div class="auth screen--plain">
    <div class="auth__logo">
      <div class="auth__mark">MS</div>
      <h1 class="auth__title">MS-Factory</h1>
      <p class="auth__sub">محاسبة سحابية لشركتك — من أي مكان</p>
    </div>
    <form novalidate>
      ${isLogin ? "" : `<div class="field"><label class="field__label" for="full_name">الاسم</label>
        <input class="input" id="full_name" name="full_name" autocomplete="name" placeholder="محمد أحمد" /></div>`}
      <div class="field">
        <label class="field__label" for="email">البريد الإلكتروني</label>
        <input class="input" id="email" name="email" type="email" inputmode="email" dir="ltr"
          autocomplete="email" required placeholder="you@company.com" />
      </div>
      <div class="field">
        <label class="field__label" for="password">كلمة المرور</label>
        <input class="input" id="password" name="password" type="password" dir="ltr"
          autocomplete="${isLogin ? "current-password" : "new-password"}" required
          placeholder="${isLogin ? "••••••••" : "8 أحرف على الأقل"}" />
      </div>
      ${isLogin ? "" : `<div class="field"><label class="field__label" for="company_name">اسم الشركة</label>
        <input class="input" id="company_name" name="company_name" required placeholder="شركة النور للتجارة" /></div>
      <div class="field"><label class="field__label" for="country">الدولة</label>
        <select class="select" id="country" name="country">
          <option value="EG" selected>مصر</option><option value="SA">السعودية</option>
          <option value="AE">الإمارات</option><option value="KW">الكويت</option>
        </select></div>`}
      <button class="btn btn--block" type="submit">${isLogin ? "تسجيل الدخول" : "إنشاء الحساب"}</button>
    </form>
    <p class="auth__switch">
      ${isLogin ? "ليس لديك حساب؟" : "لديك حساب بالفعل؟"}
      <button type="button" data-mode="${isLogin ? "register" : "login"}">
        ${isLogin ? "أنشئ حساباً جديداً" : "سجّل الدخول"}</button>
    </p>
  </div>`);

  view.querySelector("[data-mode]").onclick = (event) => renderAuth(event.target.dataset.mode);
  view.querySelector("form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector("button[type=submit]");
    const data = Object.fromEntries(new FormData(form));
    button.disabled = true;
    button.textContent = "جارٍ...";
    try {
      if (isLogin) {
        store.save(await api("/auth/login", { method: "POST", body: { email: data.email, password: data.password } }));
      } else {
        store.save(
          await api("/auth/register", {
            method: "POST",
            body: {
              email: data.email,
              password: data.password,
              full_name: data.full_name,
              company_name: data.company_name,
              country: data.country,
            },
          })
        );
      }
      await bootstrap();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
      button.textContent = isLogin ? "تسجيل الدخول" : "إنشاء الحساب";
    }
  };
  setScreen(view, { plain: true });
}

// ---------------------------------------------------------------- data loading

async function loadAccounts(force = false) {
  if (!store.cache.accounts || force) store.cache.accounts = await api("/accounts");
  return store.cache.accounts;
}

async function loadContacts(force = false) {
  if (!store.cache.contacts || force) store.cache.contacts = await api("/contacts");
  return store.cache.contacts;
}

const contactName = (id) => store.cache.contacts?.find((c) => c.id === id)?.name || `#${id}`;

// ---------------------------------------------------------------- dashboard

async function renderDashboard() {
  setScreen(spinner(), {
    appbar: appbar({ title: companyName(), subtitle: store.company?.base_currency }),
    tabbar: tabbar(),
  });
  try {
    const [data, invoices] = await Promise.all([api("/reports/dashboard"), api("/invoices")]);
    await loadContacts(); // the recent-invoice list shows contact names
    const recent = invoices.slice(0, 4);
    // A partially-paid invoice that is past due is still overdue money, and what
    // is owed is the outstanding balance — not the invoice total.
    const overdue = invoices
      .filter((i) => ["issued", "partially_paid"].includes(i.status) && i.due_date && i.due_date < today())
      .map((i) => ({ ...i, outstanding: Number(i.total) - Number(i.amount_paid) }))
      .filter((i) => i.outstanding > 0);
    const overdueTotal = overdue.reduce((sum, i) => sum + i.outstanding, 0);

    const view = el(`<div>
      <div class="kpi-grid">
        <div class="kpi kpi--income"><div class="kpi__label">الإيرادات</div>
          <div class="kpi__value num">${money(data.income)}</div></div>
        <div class="kpi kpi--expense"><div class="kpi__label">المصروفات</div>
          <div class="kpi__value num">${money(data.expense)}</div></div>
        <div class="kpi"><div class="kpi__label">صافي الربح</div>
          <div class="kpi__value num">${money(data.net_profit)}</div></div>
        <div class="kpi kpi--assets"><div class="kpi__label">النقدية</div>
          <div class="kpi__value num">${money(data.cash)}</div></div>
      </div>

      ${overdue.length ? `<div class="card" style="border-inline-start:4px solid var(--danger-fg)">
        <div class="card__head"><h2 class="card__title">⚠️ فواتير متأخرة</h2>
          <a class="btn btn--ghost btn--sm" href="#/invoices">عرض</a></div>
        <div class="list__meta">${overdue.length} فاتورة متأخرة بإجمالي
          <strong class="num">${money(overdueTotal)}</strong></div>
      </div>` : ""}

      <div class="section-title">أحدث الفواتير</div>
      <div class="card card--flush">
        ${recent.length
          ? `<ul class="list">${recent
              .map(
                (invoice) => `<li><a class="list__item" href="#/invoices/${invoice.id}">
                <div class="list__main">
                  <div class="list__title">${escapeHtml(contactName(invoice.contact_id))}</div>
                  <div class="list__meta"><span class="num">${escapeHtml(invoice.number)}</span> · ${date(invoice.issue_date)}</div>
                </div>
                <div style="text-align:end">
                  <div class="list__value num">${money(invoice.total, invoice.currency)}</div>
                  <div style="margin-top:4px">${statusBadge(invoice.status)}</div>
                </div></a></li>`
              )
              .join("")}</ul>`
          : emptyState("🧾", "لا توجد فواتير بعد", "ابدأ بإنشاء أول فاتورة بيع")}
      </div>

      <div class="action-bar">
        <a class="btn" href="#/invoices/new">＋ فاتورة جديدة</a>
        <a class="btn btn--ghost" href="#/payments?new=receive">💵 تسجيل دفعة</a>
      </div>
    </div>`);

    setScreen(view, {
      appbar: appbar({
        title: companyName(),
        subtitle: `${store.role === "owner" ? "مالك" : store.role} · ${store.memberships.length > 1 ? "اضغط لتبديل الشركة" : ""}`,
        actions: `<button class="icon-btn" data-action="menu" aria-label="القائمة">☰</button>`,
      }),
      tabbar: tabbar(),
    });
  } catch (error) {
    setScreen(emptyState("⚠️", "تعذّر تحميل البيانات", error.message), { tabbar: tabbar() });
  }
}

// ---------------------------------------------------------------- invoices

async function renderInvoices() {
  setScreen(spinner(), { appbar: appbar({ title: "الفواتير" }), tabbar: tabbar() });
  try {
    const invoices = await api("/invoices");
    await loadContacts();

    const rows = invoices
      .map(
        (invoice) => `<li><a class="list__item" href="#/invoices/${invoice.id}">
        <div class="list__main">
          <div class="list__title">${escapeHtml(contactName(invoice.contact_id))}</div>
          <div class="list__meta">${escapeHtml(invoice.number)} · ${date(invoice.issue_date)}
            · ${invoice.kind === "sales" ? "بيع" : "شراء"}</div>
        </div>
        <div style="text-align:end">
          <div class="list__value num">${money(invoice.total, invoice.currency)}</div>
          <div style="margin-top:4px">${statusBadge(invoice.status)}</div>
        </div></a></li>`
      )
      .join("");

    setScreen(
      el(`<div>
        <div class="card card--flush">
          ${invoices.length ? `<ul class="list">${rows}</ul>` : emptyState("🧾", "لا توجد فواتير", "أنشئ فاتورة بيع أو شراء")}
        </div>
        <div class="action-bar"><a class="btn" href="#/invoices/new">＋ فاتورة جديدة</a></div>
      </div>`),
      { appbar: appbar({ title: "الفواتير" }), tabbar: tabbar() }
    );
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
}

async function renderInvoiceForm() {
  const [accounts, contacts] = await Promise.all([loadAccounts(), loadContacts()]);
  const customers = contacts.filter((c) => c.kind === "customer");

  // Tax rates come from the company's own tax codes, so changing VAT is a
  // settings edit — not a code change.
  let taxCodes = [];
  try {
    taxCodes = (await loadSettings()).tax_codes.filter((c) => c.is_active);
  } catch {
    taxCodes = [];
  }
  if (!taxCodes.length) taxCodes = [{ id: 0, name_ar: "بدون ضريبة", rate: 0 }];
  const defaultRate = taxCodes[0] ? Number(taxCodes[0].rate) : 0;

  const view = el(`<form novalidate>
    <div class="segment">
      <button type="button" class="segment__btn" data-kind="sales" aria-pressed="true">فاتورة بيع</button>
      <button type="button" class="segment__btn" data-kind="purchase" aria-pressed="false">فاتورة شراء</button>
    </div>

    <div class="card">
      <div class="field"><label class="field__label" for="contact">العميل / المورد</label>
        <select class="select" id="contact" required>
          <option value="">— اختر —</option>
          ${(customers.length ? customers : contacts)
            .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
            .join("")}
        </select>
        ${contacts.length ? "" : '<div class="field__hint">لا يوجد عملاء — أضف عميلاً أولاً من تبويب العملاء</div>'}
      </div>
      <div class="line-editor__row">
        <div class="field"><label class="field__label" for="issue_date">تاريخ الفاتورة</label>
          <input class="input" type="date" id="issue_date" value="${today()}" required /></div>
        <div class="field"><label class="field__label" for="due_date">تاريخ الاستحقاق</label>
          <input class="input" type="date" id="due_date" /></div>
      </div>
    </div>

    <div class="section-title">بنود الفاتورة</div>
    <div id="lines"></div>
    <button type="button" class="btn btn--ghost btn--block" data-add-line>＋ إضافة بند</button>

    <div class="card" style="margin-top:12px">
      <div class="totals">
        <div class="totals__row"><span>المجموع</span><span class="num" data-subtotal>0.00</span></div>
        <div class="totals__row"><span>الضريبة</span><span class="num" data-tax>0.00</span></div>
        <div class="totals__row totals__row--grand"><span>الإجمالي</span><span class="num" data-total>0.00</span></div>
      </div>
    </div>

    <div class="field"><label class="field__label" for="notes">ملاحظات</label>
      <textarea class="textarea" id="notes" placeholder="اختياري"></textarea></div>

    <div class="action-bar">
      <button class="btn" type="submit">حفظ الفاتورة</button>
      <button class="btn btn--ghost" type="button" data-nav="#/invoices">إلغاء</button>
    </div>
  </form>`);

  let kind = "sales";
  const linesHost = view.querySelector("#lines");

  function addLine(focus = false) {
    const node = el(`<div class="line-editor">
      <div class="line-editor__head"><span>بند</span>
        <button type="button" class="btn btn--ghost btn--sm" data-remove>حذف</button></div>
      <div class="field"><label class="field__label">الوصف</label>
        <input class="input" data-description placeholder="وصف البند" /></div>
      <div class="line-editor__row">
        <div class="field"><label class="field__label">الكمية</label>
          <input class="input num" data-quantity type="number" step="0.01" min="0" value="1" inputmode="decimal" /></div>
        <div class="field"><label class="field__label">سعر الوحدة</label>
          <input class="input num" data-unit-price type="number" step="0.01" min="0" value="0" inputmode="decimal" /></div>
      </div>
      <div class="line-editor__row">
        <div class="field"><label class="field__label">خصم %</label>
          <input class="input num" data-discount type="number" step="0.01" min="0" max="100" value="0" inputmode="decimal" /></div>
        <div class="field"><label class="field__label">ضريبة %</label>
          <select class="select" data-tax-rate>
            ${taxCodes
              .map(
                (code) =>
                  `<option value="${Number(code.rate)}" ${
                    Number(code.rate) === defaultRate ? "selected" : ""
                  }>${escapeHtml(code.name_ar || code.name)}</option>`
              )
              .join("")}
          </select></div>
      </div>
    </div>`);
    node.querySelector("[data-remove]").onclick = () => {
      node.remove();
      recalc();
    };
    node.addEventListener("input", recalc);
    linesHost.appendChild(node);
    if (focus) node.querySelector("[data-description]").focus();
  }

  function collect() {
    return [...linesHost.querySelectorAll(".line-editor")].map((node) => ({
      description: node.querySelector("[data-description]").value.trim(),
      quantity: Number(node.querySelector("[data-quantity]").value) || 0,
      unit_price: Number(node.querySelector("[data-unit-price]").value) || 0,
      discount_rate: Number(node.querySelector("[data-discount]").value) || 0,
      tax_rate: Number(node.querySelector("[data-tax-rate]").value) || 0,
    }));
  }

  function recalc() {
    let subtotal = 0;
    let tax = 0;
    for (const line of collect()) {
      const gross = line.quantity * line.unit_price;
      const net = gross - gross * (line.discount_rate / 100);
      subtotal += net;
      tax += net * (line.tax_rate / 100);
    }
    view.querySelector("[data-subtotal]").textContent = money(subtotal);
    view.querySelector("[data-tax]").textContent = money(tax);
    view.querySelector("[data-total]").textContent = money(subtotal + tax);
  }

  view.querySelector("[data-add-line]").onclick = () => addLine(true);
  view.querySelectorAll("[data-kind]").forEach((button) => {
    button.onclick = () => {
      kind = button.dataset.kind;
      view.querySelectorAll("[data-kind]").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    };
  });

  view.onsubmit = async (event) => {
    event.preventDefault();
    const submit = view.querySelector("button[type=submit]");
    const contactId = Number(view.querySelector("#contact").value);
    const lines = collect();

    if (!contactId) return toast("اختر العميل أولاً", "error");
    if (!lines.length) return toast("أضف بنداً واحداً على الأقل", "error");
    if (lines.some((line) => line.quantity <= 0)) return toast("الكمية يجب أن تكون أكبر من صفر", "error");

    const payload = {
      kind,
      contact_id: contactId,
      issue_date: view.querySelector("#issue_date").value,
      due_date: view.querySelector("#due_date").value || null,
      notes: view.querySelector("#notes").value.trim(),
      lines,
    };

    submit.disabled = true;
    submit.textContent = "جارٍ الحفظ...";
    try {
      const invoice = await api("/invoices", {
        method: "POST",
        body: payload,
        headers: { "Idempotency-Key": idempotencyKey("inv", payload) },
      });
      toast(`تم حفظ الفاتورة ${invoice.number}`, "success");
      location.hash = `#/invoices/${invoice.id}`;
    } catch (error) {
      toast(error.message, "error");
      submit.disabled = false;
      submit.textContent = "حفظ الفاتورة";
    }
  };

  addLine();
  recalc();
  setScreen(view, { appbar: appbar({ title: "فاتورة جديدة", back: "#/invoices" }) });
}

async function renderInvoiceDetail(invoiceId) {
  setScreen(spinner(), { appbar: appbar({ title: "الفاتورة", back: "#/invoices" }) });
  try {
    const [invoice, accounts] = await Promise.all([api(`/invoices/${invoiceId}`), loadAccounts()]);
    await loadContacts();
    const outstanding = Number(invoice.total) - Number(invoice.amount_paid);
    const cashAccounts = accounts.filter((a) => ["1010", "1020"].includes(a.code));

    const view = el(`<div>
      <div class="card">
        <div class="card__head">
          <div><h2 class="card__title">${escapeHtml(invoice.number)}</h2>
            <div class="list__meta">${escapeHtml(contactName(invoice.contact_id))}</div></div>
          ${statusBadge(invoice.status)}
        </div>
        <div class="totals__row"><span>تاريخ الإصدار</span><span class="num">${date(invoice.issue_date)}</span></div>
        <div class="totals__row"><span>تاريخ الاستحقاق</span><span class="num">${date(invoice.due_date)}</span></div>
      </div>

      <div class="section-title">البنود</div>
      <div class="card card--flush table-wrap">
        <table>
          <thead><tr><th>الوصف</th><th class="num">الكمية</th><th class="num">السعر</th><th class="num">الإجمالي</th></tr></thead>
          <tbody>${invoice.lines
            .map((line) => {
              const gross = Number(line.quantity) * Number(line.unit_price);
              const net = gross - gross * (Number(line.discount_rate) / 100);
              const total = net + net * (Number(line.tax_rate) / 100);
              return `<tr><td>${escapeHtml(line.description || "—")}</td>
                <td class="num">${Number(line.quantity)}</td>
                <td class="num">${money(line.unit_price)}</td>
                <td class="num">${money(total)}</td></tr>`;
            })
            .join("")}</tbody>
        </table>
      </div>

      <div class="card">
        <div class="totals__row"><span>المجموع</span><span class="num">${money(invoice.subtotal)}</span></div>
        <div class="totals__row"><span>الضريبة</span><span class="num">${money(invoice.tax_total)}</span></div>
        <div class="totals__row totals__row--grand"><span>الإجمالي</span><span class="num">${money(invoice.total)}</span></div>
        <div class="totals__row"><span>المدفوع</span><span class="num">${money(invoice.amount_paid)}</span></div>
        <div class="totals__row" style="font-weight:700"><span>المتبقي</span><span class="num">${money(outstanding)}</span></div>
      </div>

      ${invoice.notes ? `<div class="card"><div class="card__title">ملاحظات</div>
        <p style="margin:8px 0 0;color:var(--ink-700)">${escapeHtml(invoice.notes)}</p></div>` : ""}

      <div class="row-actions">
        ${invoice.status !== "void" && outstanding > 0 && store.can("payment.manage")
          ? '<button class="btn" data-pay>💵 تسجيل دفعة</button>' : ""}
        ${invoice.status !== "void" && store.can("invoice.manage")
          ? '<button class="btn btn--danger" data-void>إلغاء الفاتورة</button>' : ""}
      </div>
    </div>`);

    const payButton = view.querySelector("[data-pay]");
    if (payButton) {
      payButton.onclick = () => {
        let close = () => {};
        close = openSheet(
          "تسجيل دفعة",
          buildPaymentForm({
            kind: invoice.kind === "sales" ? "receive" : "spend",
            contactId: invoice.contact_id,
            amount: outstanding,
            cashAccounts,
            invoiceId: invoice.id,
            onDone: () => renderInvoiceDetail(invoiceId),
            onClose: () => close(),
          })
        );
      };
    }

    const voidButton = view.querySelector("[data-void]");
    if (voidButton) {
      voidButton.onclick = async () => {
        if (!(await confirmDialog("سيتم إنشاء قيد عكسي وإلغاء الفاتورة. متابعة؟"))) return;
        try {
          await api(`/invoices/${invoiceId}/void`, { method: "POST", body: { reason: "إلغاء من التطبيق" } });
          toast("تم إلغاء الفاتورة", "success");
          renderInvoiceDetail(invoiceId);
        } catch (error) {
          toast(error.message, "error");
        }
      };
    }

    setScreen(view, { appbar: appbar({ title: invoice.number, back: "#/invoices" }) });
  } catch (error) {
    setScreen(emptyState("⚠️", "تعذّر تحميل الفاتورة", error.message), { tabbar: tabbar() });
  }
}

// ---------------------------------------------------------------- payments

function buildPaymentForm({ kind, contactId, amount, cashAccounts, invoiceId, onDone, onClose }) {
  const form = el(`<form novalidate>
    <div class="field"><label class="field__label" for="amount">المبلغ</label>
      <input class="input num" id="amount" type="number" step="0.01" min="0.01" inputmode="decimal"
        value="${amount ? Number(amount).toFixed(2) : ""}" required /></div>
    <div class="field"><label class="field__label" for="date">التاريخ</label>
      <input class="input" id="date" type="date" value="${today()}" required /></div>
    <div class="field"><label class="field__label" for="account">الحساب</label>
      <select class="select" id="account" required>
        ${cashAccounts.map((a) => `<option value="${a.id}">${escapeHtml(a.name_ar || a.name)}</option>`).join("")}
      </select></div>
    <div class="field"><label class="field__label" for="reference">المرجع</label>
      <input class="input" id="reference" placeholder="رقم التحويل أو الشيك (اختياري)" /></div>
    <button class="btn btn--block" type="submit">حفظ الدفعة</button>
  </form>`);

  form.onsubmit = async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    const payload = {
      kind,
      contact_id: contactId,
      date: form.querySelector("#date").value,
      amount: Number(form.querySelector("#amount").value),
      bank_account_id: Number(form.querySelector("#account").value),
      reference: form.querySelector("#reference").value.trim(),
      ...(invoiceId ? { invoice_id: Number(invoiceId) } : {}),
    };
    if (!(payload.amount > 0)) return toast("أدخل مبلغاً صحيحاً", "error");

    button.disabled = true;
    button.textContent = "جارٍ الحفظ...";
    try {
      await api("/payments", {
        method: "POST",
        body: payload,
        headers: { "Idempotency-Key": idempotencyKey("pay", payload) },
      });
      toast("تم تسجيل الدفعة", "success");
      onClose?.();
      await onDone();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
      button.textContent = "حفظ الدفعة";
    }
  };
  return form;
}

async function renderPayments({ autoNew } = {}) {
  setScreen(spinner(), { appbar: appbar({ title: "المدفوعات" }), tabbar: tabbar() });
  try {
    const [payments, accounts] = await Promise.all([api("/payments"), loadAccounts()]);
    await loadContacts();
    const cashAccounts = accounts.filter((a) => ["1010", "1020"].includes(a.code));

    const rows = payments
      .map(
        (payment) => `<li><div class="list__item">
        <div class="list__main">
          <div class="list__title">${escapeHtml(contactName(payment.contact_id))}</div>
          <div class="list__meta">${escapeHtml(payment.number)} · ${date(payment.date)}
            · ${payment.kind === "receive" ? "تحصيل" : "صرف"}</div>
        </div>
        <div class="list__value num" style="color:${payment.kind === "receive" ? "var(--ok-fg)" : "var(--danger-fg)"}">
          ${money(payment.amount)}</div></div></li>`
      )
      .join("");

    setScreen(
      el(`<div>
        <div class="card card--flush">
          ${payments.length ? `<ul class="list">${rows}</ul>` : emptyState("💵", "لا توجد مدفوعات", "سجّل تحصيلاً من عميل أو دفعة لمورد")}
        </div>
        <div class="action-bar">
          <button class="btn" data-new="receive">＋ تحصيل من عميل</button>
          <button class="btn btn--ghost" data-new="spend">＋ صرف لمورد</button>
        </div>
      </div>`),
      { appbar: appbar({ title: "المدفوعات" }), tabbar: tabbar() }
    );

    document.querySelectorAll("[data-new]").forEach((button) => {
      button.onclick = () => startNewPayment(button.dataset.new, cashAccounts);
    });

    if (autoNew === "receive" || autoNew === "spend") startNewPayment(autoNew, cashAccounts);
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
}

/** Two-step flow: pick the contact and amount, then confirm the payment details. */
function startNewPayment(kind, cashAccounts) {
  const wanted = kind === "receive" ? "customer" : "supplier";
  const options = (store.cache.contacts || []).filter((c) => c.kind === wanted);
  if (!options.length) {
    toast(`أضف ${kind === "receive" ? "عميلاً" : "مورداً"} أولاً`, "error");
    return;
  }

  const picker = el(`<form novalidate>
    <div class="field"><label class="field__label">${kind === "receive" ? "العميل" : "المورد"}</label>
      <select class="select" id="contact" required>
        ${options.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("")}
      </select></div>
    <div class="field"><label class="field__label">المبلغ</label>
      <input class="input num" id="amount" type="number" step="0.01" min="0.01" inputmode="decimal" required /></div>
    <button class="btn btn--block" type="submit">متابعة</button>
  </form>`);

  const closePicker = openSheet(kind === "receive" ? "تحصيل من عميل" : "صرف لمورد", picker);
  picker.onsubmit = (event) => {
    event.preventDefault();
    const amount = Number(picker.querySelector("#amount").value);
    if (!(amount > 0)) return toast("أدخل مبلغاً صحيحاً", "error");
    const contactId = Number(picker.querySelector("#contact").value);
    closePicker();
    let closePayment = () => {};
    closePayment = openSheet(
      "تفاصيل الدفعة",
      buildPaymentForm({
        kind,
        contactId,
        amount,
        cashAccounts,
        onDone: () => renderPayments(),
        onClose: () => closePayment(),
      })
    );
  };
}

// ---------------------------------------------------------------- contacts

async function renderContacts() {
  setScreen(spinner(), { appbar: appbar({ title: "العملاء والموردون" }), tabbar: tabbar() });
  try {
    const contacts = await loadContacts(true);
    const rows = contacts
      .map(
        (contact) => `<li><a class="list__item" href="#/contacts/${contact.id}">
        <div class="list__main">
          <div class="list__title">${escapeHtml(contact.name)}</div>
          <div class="list__meta">${contact.kind === "customer" ? "عميل" : "مورد"}
            ${contact.phone ? `· <span class="num">${escapeHtml(contact.phone)}</span>` : ""}</div>
        </div><span style="color:var(--ink-400)">‹</span></a></li>`
      )
      .join("");

    setScreen(
      el(`<div>
        <div class="card card--flush">
          ${contacts.length ? `<ul class="list">${rows}</ul>` : emptyState("👥", "لا يوجد عملاء", "أضف عميلاً أو مورداً للبدء")}
        </div>
        <div class="action-bar"><button class="btn" data-add>＋ جهة جديدة</button></div>
      </div>`),
      { appbar: appbar({ title: "العملاء والموردون" }), tabbar: tabbar() }
    );

    document.querySelector("[data-add]").onclick = () => {
      const form = el(`<form novalidate>
        <div class="segment">
          <button type="button" class="segment__btn" data-kind="customer" aria-pressed="true">عميل</button>
          <button type="button" class="segment__btn" data-kind="supplier" aria-pressed="false">مورد</button>
        </div>
        <div class="field"><label class="field__label">الاسم</label>
          <input class="input" id="name" required placeholder="اسم الجهة" /></div>
        <div class="field"><label class="field__label">الهاتف</label>
          <input class="input num" id="phone" inputmode="tel" placeholder="01xxxxxxxxx" /></div>
        <div class="field"><label class="field__label">الرقم الضريبي</label>
          <input class="input num" id="tax_number" placeholder="اختياري" /></div>
        <div class="field"><label class="field__label">مدة السداد (يوم)</label>
          <input class="input num" id="terms" type="number" value="30" min="0" inputmode="numeric" /></div>
        <button class="btn btn--block" type="submit">حفظ</button>
      </form>`);

      let kind = "customer";
      form.querySelectorAll("[data-kind]").forEach((button) => {
        button.onclick = () => {
          kind = button.dataset.kind;
          form.querySelectorAll("[data-kind]").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
        };
      });

      const close = openSheet("جهة جديدة", form);
      form.onsubmit = async (event) => {
        event.preventDefault();
        const submit = form.querySelector("button[type=submit]");
        submit.disabled = true;
        try {
          await api("/contacts", {
            method: "POST",
            body: {
              name: form.querySelector("#name").value.trim(),
              kind,
              phone: form.querySelector("#phone").value.trim(),
              tax_number: form.querySelector("#tax_number").value.trim(),
              payment_terms_days: Number(form.querySelector("#terms").value) || 30,
            },
          });
          toast("تمت الإضافة", "success");
          close();
          renderContacts();
        } catch (error) {
          toast(error.message, "error");
          submit.disabled = false;
        }
      };
    };
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
}

async function renderContactDetail(contactId) {
  setScreen(spinner(), { appbar: appbar({ title: "كشف حساب", back: "#/contacts" }) });
  try {
    const data = await api(`/contacts/${contactId}/statement`);
    const view = el(`<div>
      <div class="card">
        <h2 class="card__title">${escapeHtml(data.contact.name)}</h2>
        <div class="list__meta">${data.contact.kind === "customer" ? "عميل" : "مورد"}
          ${data.contact.phone ? `· <span class="num">${escapeHtml(data.contact.phone)}</span>` : ""}</div>
        <div class="totals" style="border-top:0;margin-top:12px">
          <div class="totals__row totals__row--grand"><span>الرصيد المستحق</span>
            <span class="num">${money(data.balance)}</span></div>
        </div>
      </div>

      <div class="section-title">الحركات</div>
      <div class="card card--flush table-wrap">
        ${data.movements.length
          ? `<table><thead><tr><th>التاريخ</th><th>البيان</th><th class="num">مدين</th><th class="num">دائن</th><th class="num">الرصيد</th></tr></thead>
            <tbody>${data.movements
              .map(
                (row) => `<tr><td class="num">${date(row.date)}</td>
                <td>${escapeHtml(row.description || row.reference || "—")}</td>
                <td class="num">${Number(row.debit) ? money(row.debit) : "—"}</td>
                <td class="num">${Number(row.credit) ? money(row.credit) : "—"}</td>
                <td class="num">${money(row.balance)}</td></tr>`
              )
              .join("")}</tbody></table>`
          : emptyState("📄", "لا توجد حركات", "لم تُسجّل أي فواتير أو مدفوعات لهذه الجهة")}
      </div>

      <div class="section-title">الفواتير (${data.invoices.length})</div>
      <div class="card card--flush">
        ${data.invoices.length
          ? `<ul class="list">${data.invoices
              .map(
                (invoice) => `<li><a class="list__item" href="#/invoices/${invoice.id}">
                <div class="list__main"><div class="list__title num">${escapeHtml(invoice.number)}</div></div>
                <div style="text-align:end"><div class="list__value num">${money(invoice.total)}</div>
                  <div style="margin-top:4px">${statusBadge(invoice.status)}</div></div></a></li>`
              )
              .join("")}</ul>`
          : '<div class="list__meta" style="padding:16px">لا توجد فواتير</div>'}
      </div>
    </div>`);
    setScreen(view, { appbar: appbar({ title: data.contact.name, back: "#/contacts" }) });
  } catch (error) {
    setScreen(emptyState("⚠️", "تعذّر تحميل الكشف", error.message), { tabbar: tabbar() });
  }
}

// ---------------------------------------------------------------- reports

const REPORTS = [
  { key: "income-statement", label: "قائمة الدخل", icon: "📈" },
  { key: "balance-sheet", label: "الميزانية العمومية", icon: "⚖️" },
  { key: "trial-balance", label: "ميزان المراجعة", icon: "📋" },
  { key: "ar-aging", label: "أعمار الديون", icon: "⏳" },
];

async function renderReports() {
  setScreen(
    el(`<div>
      <div class="card card--flush">
        <ul class="list">
          ${REPORTS.map(
            (report) => `<li><a class="list__item" href="#/reports/${report.key}">
              <span style="font-size:1.4rem">${report.icon}</span>
              <div class="list__main"><div class="list__title">${report.label}</div></div>
              <span style="color:var(--ink-400)">‹</span></a></li>`
          ).join("")}
        </ul>
      </div>
      <div class="section-title">الدفتر اليومي</div>
      <div class="card card--flush">
        <ul class="list"><li><a class="list__item" href="#/journal">
          <span style="font-size:1.4rem">📒</span>
          <div class="list__main"><div class="list__title">القيود المحاسبية</div>
            <div class="list__meta">عرض وإضافة قيود يدوية</div></div>
          <span style="color:var(--ink-400)">‹</span></a></li></ul>
      </div>
    </div>`),
    { appbar: appbar({ title: "التقارير" }), tabbar: tabbar() }
  );
}

async function renderReport(key) {
  setScreen(spinner(), { appbar: appbar({ title: REPORTS.find((r) => r.key === key)?.label || "تقرير", back: "#/reports" }) });
  try {
    const data = await api(`/reports/${key}`);
    let view;

    if (key === "income-statement") {
      view = el(`<div>
        <div class="kpi-grid">
          <div class="kpi kpi--income"><div class="kpi__label">الإيرادات</div><div class="kpi__value num">${money(data.income)}</div></div>
          <div class="kpi kpi--expense"><div class="kpi__label">المصروفات</div><div class="kpi__value num">${money(data.expense)}</div></div>
        </div>
        <div class="card" style="margin-top:12px">
          <div class="totals__row totals__row--grand"><span>صافي الربح</span><span class="num">${money(data.net_profit)}</span></div>
        </div></div>`);
    } else if (key === "balance-sheet") {
      view = el(`<div>
        <div class="card">
          <div class="totals__row"><span>الأصول</span><span class="num">${money(data.assets)}</span></div>
          <div class="totals__row"><span>الالتزامات</span><span class="num">${money(data.liabilities)}</span></div>
          <div class="totals__row"><span>رأس المال</span><span class="num">${money(data.equity)}</span></div>
          <div class="totals__row"><span>الأرباح المحتجزة</span><span class="num">${money(data.net_profit)}</span></div>
          <div class="totals__row totals__row--grand"><span>إجمالي الالتزامات + حقوق الملكية</span>
            <span class="num">${money(data.total_liabilities_equity)}</span></div>
        </div>
        <div class="card" style="text-align:center;border-inline-start:4px solid var(--${data.balanced ? "ok" : "danger"}-fg)">
          <strong>${data.balanced ? "✓ الميزانية متوازنة" : "⚠️ الميزانية غير متوازنة"}</strong></div></div>`);
    } else if (key === "trial-balance") {
      view = el(`<div class="card card--flush table-wrap">
        <table><thead><tr><th>الحساب</th><th class="num">مدين</th><th class="num">دائن</th><th class="num">الرصيد</th></tr></thead>
        <tbody>${data.rows
          .map(
            (row) => `<tr><td>${escapeHtml(row.name_ar || row.name)}
            <div class="list__meta num">${escapeHtml(row.code)}</div></td>
            <td class="num">${Number(row.debit) ? money(row.debit) : "—"}</td>
            <td class="num">${Number(row.credit) ? money(row.credit) : "—"}</td>
            <td class="num">${money(row.balance)}</td></tr>`
          )
          .join("")}
        <tr class="total-row"><td>الإجمالي</td><td class="num">${money(data.total_debit)}</td>
          <td class="num">${money(data.total_credit)}</td><td></td></tr></tbody></table></div>`);
    } else {
      view = el(`<div>
        <div class="kpi-grid">
          <div class="kpi"><div class="kpi__label">جاري</div><div class="kpi__value num">${money(data.buckets.current)}</div></div>
          <div class="kpi"><div class="kpi__label">1–30 يوم</div><div class="kpi__value num">${money(data.buckets["1_30"])}</div></div>
          <div class="kpi"><div class="kpi__label">31–60 يوم</div><div class="kpi__value num">${money(data.buckets["31_60"])}</div></div>
          <div class="kpi"><div class="kpi__label">أكثر من 60</div><div class="kpi__value num">${money(data.buckets["60_plus"])}</div></div>
        </div>
        <div class="section-title">تفصيل حسب الجهة</div>
        <div class="card card--flush table-wrap">
          ${data.contacts.length
            ? `<table><thead><tr><th>الجهة</th><th class="num">جاري</th><th class="num">1–30</th>
              <th class="num">31–60</th><th class="num">+60</th><th class="num">الإجمالي</th></tr></thead>
              <tbody>${data.contacts
                .map(
                  (row) => `<tr><td>${escapeHtml(row.name)}</td>
                  <td class="num">${money(row.current)}</td><td class="num">${money(row["1_30"])}</td>
                  <td class="num">${money(row["31_60"])}</td><td class="num">${money(row["60_plus"])}</td>
                  <td class="num"><strong>${money(row.total)}</strong></td></tr>`
                )
                .join("")}</tbody></table>`
            : emptyState("✓", "لا توجد ديون مستحقة", "كل الفواتير محصّلة")}
        </div></div>`);
    }

    setScreen(view, { appbar: appbar({ title: REPORTS.find((r) => r.key === key)?.label, back: "#/reports" }) });
  } catch (error) {
    setScreen(emptyState("⚠️", "تعذّر تحميل التقرير", error.message), { tabbar: tabbar() });
  }
}

// ---------------------------------------------------------------- journal

async function renderJournal() {
  setScreen(spinner(), { appbar: appbar({ title: "القيود المحاسبية", back: "#/reports" }) });
  try {
    const entries = await api("/journal");
    const view = el(`<div>
      <div class="card card--flush">
        ${entries.length
          ? `<ul class="list">${entries
              .map(
                (entry) => `<li><div class="list__item">
                <div class="list__main">
                  <div class="list__title">${escapeHtml(entry.memo || entry.reference || "قيد")}</div>
                  <div class="list__meta"><span class="num">${date(entry.entry_date)}</span>
                    · ${entry.source_type === "manual" ? "يدوي" : escapeHtml(entry.source_type)}
                    ${entry.reverses_entry_id ? " · قيد عكسي" : ""}</div>
                </div>
                <div class="list__value num">${money(
                  entry.lines.reduce((sum, line) => sum + Number(line.debit), 0)
                )}</div></div></li>`
              )
              .join("")}</ul>`
          : emptyState("📒", "لا توجد قيود", "أنشئ قيداً يدوياً")}
      </div>
      <div class="action-bar"><a class="btn" href="#/journal/new">＋ قيد يدوي</a></div>
    </div>`);
    setScreen(view, { appbar: appbar({ title: "القيود المحاسبية", back: "#/reports" }) });
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
}

async function renderJournalForm() {
  const accounts = await loadAccounts();
  const view = el(`<form novalidate>
    <div class="card">
      <div class="field"><label class="field__label">التاريخ</label>
        <input class="input" type="date" id="entry_date" value="${today()}" required /></div>
      <div class="field"><label class="field__label">البيان</label>
        <input class="input" id="memo" placeholder="وصف القيد" /></div>
      <div class="field"><label class="field__label">المرجع</label>
        <input class="input num" id="reference" placeholder="اختياري" /></div>
    </div>
    <div class="section-title">البنود (مدين = دائن)</div>
    <div id="lines"></div>
    <button type="button" class="btn btn--ghost btn--block" data-add>＋ إضافة سطر</button>
    <div class="card" style="margin-top:12px">
      <div class="totals__row"><span>إجمالي المدين</span><span class="num" data-debits>0.00</span></div>
      <div class="totals__row"><span>إجمالي الدائن</span><span class="num" data-credits>0.00</span></div>
      <div class="totals__row totals__row--grand"><span>الفرق</span><span class="num" data-diff>0.00</span></div>
    </div>
    <div class="action-bar"><button class="btn" type="submit">ترحيل القيد</button>
      <button class="btn btn--ghost" type="button" data-nav="#/journal">إلغاء</button></div>
  </form>`);

  const linesHost = view.querySelector("#lines");
  const accountOptions = accounts
    .filter((a) => !a.is_group)
    .map((a) => `<option value="${a.id}">${escapeHtml(a.code)} — ${escapeHtml(a.name_ar || a.name)}</option>`)
    .join("");

  function addLine() {
    const node = el(`<div class="line-editor">
      <div class="line-editor__head"><span>سطر</span>
        <button type="button" class="btn btn--ghost btn--sm" data-remove>حذف</button></div>
      <div class="field"><label class="field__label">الحساب</label>
        <select class="select" data-account>${accountOptions}</select></div>
      <div class="line-editor__row">
        <div class="field"><label class="field__label">مدين</label>
          <input class="input num" data-debit type="number" step="0.01" min="0" value="0" inputmode="decimal" /></div>
        <div class="field"><label class="field__label">دائن</label>
          <input class="input num" data-credit type="number" step="0.01" min="0" value="0" inputmode="decimal" /></div>
      </div>
      <div class="field"><label class="field__label">البيان</label>
        <input class="input" data-description placeholder="اختياري" /></div>
    </div>`);
    node.querySelector("[data-remove]").onclick = () => {
      node.remove();
      recalc();
    };
    node.addEventListener("input", recalc);
    linesHost.appendChild(node);
  }

  function collect() {
    return [...linesHost.querySelectorAll(".line-editor")].map((node) => ({
      account_id: Number(node.querySelector("[data-account]").value),
      debit: Number(node.querySelector("[data-debit]").value) || 0,
      credit: Number(node.querySelector("[data-credit]").value) || 0,
      description: node.querySelector("[data-description]").value.trim(),
    }));
  }

  function recalc() {
    const lines = collect();
    const debits = lines.reduce((sum, line) => sum + line.debit, 0);
    const credits = lines.reduce((sum, line) => sum + line.credit, 0);
    const diff = debits - credits;
    view.querySelector("[data-debits]").textContent = money(debits);
    view.querySelector("[data-credits]").textContent = money(credits);
    const diffNode = view.querySelector("[data-diff]");
    diffNode.textContent = money(diff);
    diffNode.style.color = Math.abs(diff) < 0.005 ? "var(--ok-fg)" : "var(--danger-fg)";
    const submit = view.querySelector("button[type=submit]");
    submit.disabled = Math.abs(diff) >= 0.005;
  }

  view.querySelector("[data-add]").onclick = () => {
    addLine();
    addLine();
  };

  view.onsubmit = async (event) => {
    event.preventDefault();
    const submit = view.querySelector("button[type=submit]");
    submit.disabled = true;
    submit.textContent = "جارٍ الترحيل...";
    try {
      await api("/journal", {
        method: "POST",
        body: {
          entry_date: view.querySelector("#entry_date").value,
          memo: view.querySelector("#memo").value.trim(),
          reference: view.querySelector("#reference").value.trim(),
          lines: collect(),
        },
      });
      toast("تم ترحيل القيد", "success");
      location.hash = "#/journal";
    } catch (error) {
      toast(error.message, "error");
      submit.textContent = "ترحيل القيد";
      recalc();
    }
  };

  addLine();
  addLine();
  recalc();
  setScreen(view, { appbar: appbar({ title: "قيد يدوي", back: "#/journal" }) });
}

// ---------------------------------------------------------------- settings

function openMenu() {
  const close = openSheet(
    "الإعدادات",
    el(`<div>
      <div class="card">
        <div class="list__meta">مسجّل الدخول</div>
        <div class="list__title">${escapeHtml(store.user?.email || "")}</div>
        <div class="list__meta">${escapeHtml(store.user?.full_name || "")}</div>
      </div>
      <div class="section-title">الشركات</div>
      <div class="card card--flush">
        <ul class="list">${store.memberships
          .map(
            (m) => `<li><button class="list__item" data-company="${m.company.id}">
            <div class="list__main"><div class="list__title">${escapeHtml(m.company.name)}</div>
              <div class="list__meta">${m.company.base_currency} · ${m.role}</div></div>
            ${m.company.id === store.companyId ? '<span style="color:var(--brand-700)">✓</span>' : ""}
            </button></li>`
          )
          .join("")}</ul>
      </div>
      <div class="card card--flush">
        <ul class="list"><li><a class="list__item" href="#/settings" data-settings-link>
          <span style="font-size:1.4rem">⚙️</span>
          <div class="list__main"><div class="list__title">الإعدادات والضرائب</div>
            <div class="list__meta">بيانات الشركة وأكواد الضريبة</div></div>
          <span style="color:var(--ink-400)">‹</span></a></li></ul>
      </div>
      <div class="card">
        <div class="list__meta">النسخة</div><div class="list__title">MS-Factory 1.0</div>
        <div class="list__meta">المنطقة الزمنية: ${Intl.DateTimeFormat().resolvedOptions().timeZone}</div>
      </div>
      <button class="btn btn--danger btn--block" data-logout>تسجيل الخروج</button>
    </div>`)
  );

  document.querySelectorAll("[data-company]").forEach((button) => {
    button.onclick = () => {
      store.companyId = Number(button.dataset.company);
      store.cache = { accounts: null, contacts: null };
      close();
      location.hash = "#/dashboard";
      route();
    };
  });
  document.querySelector("[data-logout]").onclick = async () => {
    try {
      await api("/auth/logout", { method: "POST", body: { refresh_token: store.session.refresh_token } });
    } catch {
      /* logging out locally is enough */
    }
    store.reset();
    close();
    renderAuth("login");
  };
}

// ---------------------------------------------------------------- settings

const SETTING_FIELDS = [
  { key: "company_name", label: "اسم الشركة", type: "text" },
  { key: "tax_number", label: "الرقم الضريبي", type: "text" },
  { key: "phone", label: "الهاتف", type: "tel" },
  { key: "address", label: "العنوان", type: "text" },
  { key: "currency", label: "العملة", type: "text" },
  { key: "vat_rate", label: "نسبة الضريبة الافتراضية %", type: "number" },
  { key: "invoice_terms_days", label: "مدة السداد (أيام)", type: "number" },
  { key: "fiscal_year_start", label: "بداية السنة المالية (شهر-يوم)", type: "text" },
  { key: "invoice_footer", label: "تذييل الفاتورة", type: "text" },
];

async function loadSettings() {
  if (!store.cache.settings) store.cache.settings = await api("/settings");
  return store.cache.settings;
}

// Settings hold the company's editable identity; the membership name is only
// the fallback before settings load.
function companyName() {
  return store.cache.settings?.settings?.company_name || store.company?.name || "الرئيسية";
}

async function renderSettings() {
  setScreen(spinner(), { appbar: appbar({ title: "الإعدادات" }), tabbar: tabbar() });
  try {
    const data = await loadSettings();
    const accounts = await loadAccounts();
    const taxAccounts = accounts.filter((a) => ["2200", "2300", "1300"].includes(a.code));

    const view = el(`<div>
      <div class="section-title">بيانات الشركة والضرائب</div>
      <form class="card" data-settings-form>
        ${SETTING_FIELDS.map(
          (field) => `<div class="field"><label class="field__label">${field.label}</label>
            <input class="input${field.type === "number" ? " num" : ""}" data-key="${field.key}"
              type="${field.type}" value="${escapeHtml(data.settings[field.key] ?? "")}" /></div>`
        ).join("")}
        <button class="btn btn--block" type="submit">حفظ الإعدادات</button>
      </form>

      <div class="section-title">أكواد الضريبة</div>
      <div class="card card--flush">
        <ul class="list">${data.tax_codes
          .map(
            (code) => `<li class="list__item">
              <div class="list__main">
                <div class="list__title">${escapeHtml(code.name_ar || code.name)}
                  <span class="badge">${Number(code.rate)}%</span></div>
                <div class="list__meta">${escapeHtml(code.name)} · ${TAX_KIND_LABELS[code.kind] || code.kind}${
                  code.is_active ? "" : " · موقوف"
                }</div>
              </div>
              <button class="btn btn--ghost btn--sm" data-edit-tax="${code.id}">تعديل</button>
            </li>`
          )
          .join("")}</ul>
      </div>
      <button class="btn btn--ghost btn--block" data-add-tax>＋ إضافة كود ضريبة</button>
    </div>`);

    view.querySelector("[data-settings-form]").onsubmit = async (event) => {
      event.preventDefault();
      const button = event.target.querySelector("button[type=submit]");
      const settings = {};
      view.querySelectorAll("[data-key]").forEach((input) => {
        settings[input.dataset.key] = input.value.trim();
      });
      button.disabled = true;
      button.textContent = "جارٍ الحفظ...";
      try {
        store.cache.settings = await api("/settings", { method: "PATCH", body: { settings } });
        store.cache.accounts = null;
        toast("تم حفظ الإعدادات", "success");
        renderSettings();
      } catch (error) {
        toast(error.message, "error");
        button.disabled = false;
        button.textContent = "حفظ الإعدادات";
      }
    };

    view.querySelectorAll("[data-edit-tax]").forEach((button) => {
      const code = data.tax_codes.find((c) => c.id === Number(button.dataset.editTax));
      button.onclick = () => openTaxCodeSheet(code, taxAccounts);
    });
    view.querySelector("[data-add-tax]").onclick = () => openTaxCodeSheet(null, taxAccounts);

    setScreen(view, { appbar: appbar({ title: "الإعدادات" }), tabbar: tabbar() });
  } catch (error) {
    setScreen(emptyState("⚠️", "تعذّر تحميل الإعدادات", error.message), { tabbar: tabbar() });
  }
}

const TAX_KIND_LABELS = { vat: "ضريبة قيمة مضافة", withholding: "خصم وإضافة", other: "أخرى" };

function openTaxCodeSheet(code, taxAccounts) {
  const editing = Boolean(code);
  const form = el(`<form novalidate>
    <div class="field"><label class="field__label">الاسم بالعربية</label>
      <input class="input" id="name_ar" value="${escapeHtml(code?.name_ar || "")}" required /></div>
    <div class="field"><label class="field__label">الاسم بالإنجليزية</label>
      <input class="input" id="name" value="${escapeHtml(code?.name || "")}" /></div>
    <div class="field"><label class="field__label">النسبة %</label>
      <input class="input num" id="rate" type="number" step="0.01" min="0" inputmode="decimal"
        value="${code ? Number(code.rate) : 14}" required /></div>
    <div class="field"><label class="field__label">النوع</label>
      <select class="select" id="kind">
        ${Object.entries(TAX_KIND_LABELS)
          .map(
            ([value, label]) =>
              `<option value="${value}" ${code?.kind === value ? "selected" : ""}>${label}</option>`
          )
          .join("")}
      </select></div>
    <div class="field"><label class="field__label">حساب الضريبة</label>
      <select class="select" id="account_id">
        <option value="">— بدون —</option>
        ${taxAccounts
          .map(
            (a) =>
              `<option value="${a.id}" ${code?.account_id === a.id ? "selected" : ""}>${escapeHtml(
                a.name_ar || a.name
              )}</option>`
          )
          .join("")}
      </select></div>
    <button class="btn btn--block" type="submit">${editing ? "حفظ التعديلات" : "إضافة الكود"}</button>
    ${editing && code.is_active ? '<button class="btn btn--danger btn--block" type="button" data-retire style="margin-top:8px">إيقاف الكود</button>' : ""}
  </form>`);

  const close = openSheet(editing ? "تعديل كود ضريبة" : "كود ضريبة جديد", form);

  form.onsubmit = async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    const payload = {
      name_ar: form.querySelector("#name_ar").value.trim(),
      name: form.querySelector("#name").value.trim() || form.querySelector("#name_ar").value.trim(),
      rate: Number(form.querySelector("#rate").value),
      kind: form.querySelector("#kind").value,
      account_id: Number(form.querySelector("#account_id").value) || null,
    };
    if (!payload.name_ar) return toast("أدخل اسم الكود", "error");

    button.disabled = true;
    try {
      if (editing) {
        await api(`/settings/tax-codes/${code.id}`, { method: "PATCH", body: payload });
      } else {
        await api("/settings/tax-codes", { method: "POST", body: payload });
      }
      store.cache.settings = null;
      toast(editing ? "تم تحديث الكود" : "تمت إضافة الكود", "success");
      close();
      renderSettings();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
    }
  };

  const retire = form.querySelector("[data-retire]");
  if (retire) {
    retire.onclick = async () => {
      if (!(await confirmDialog("إيقاف كود الضريبة؟ لن يظهر في الفواتير الجديدة."))) return;
      try {
        await api(`/settings/tax-codes/${code.id}`, { method: "DELETE" });
        store.cache.settings = null;
        toast("تم إيقاف الكود", "success");
        close();
        renderSettings();
      } catch (error) {
        toast(error.message, "error");
      }
    };
  }
}

// ---------------------------------------------------------------- router

function route() {
  const hash = location.hash || "#/dashboard";
  const [path, query] = hash.split("?");
  const [, segment, param] = path.split("/");
  const params = new URLSearchParams(query || "");
  app.dataset.route = hash;

  const publicRoutes = ["login"];
  if (!store.session && !publicRoutes.includes(segment)) {
    renderAuth("login");
    return;
  }
  if (store.session && segment === "login") {
    location.hash = "#/dashboard";
    return;
  }

  switch (segment) {
    case "dashboard":
      return renderDashboard();
    case "invoices":
      return param === "new" ? renderInvoiceForm() : param ? renderInvoiceDetail(param) : renderInvoices();
    case "payments":
      return renderPayments({ autoNew: params.get("new") });
    case "contacts":
      return param ? renderContactDetail(param) : renderContacts();
    case "reports":
      return param ? renderReport(param) : renderReports();
    case "journal":
      return param === "new" ? renderJournalForm() : renderJournal();
    case "settings":
      return renderSettings();
    default:
      location.hash = "#/dashboard";
  }
}

// Global click delegation: navigation and menu actions work from any screen.
document.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-nav]");
  if (nav) {
    event.preventDefault();
    location.hash = nav.dataset.nav;
    return;
  }
  if (event.target.closest('[data-action="menu"]')) openMenu();
});

window.addEventListener("hashchange", route);
window.addEventListener("online", () => toast("تم استعادة الاتصال", "success"));
window.addEventListener("offline", () => toast("لا يوجد اتصال بالإنترنت", "error"));

async function bootstrap() {
  const me = await api("/auth/me");
  store.user = me.user;
  store.memberships = me.memberships;
  if (!store.memberships.length) {
    toast("لا توجد شركة مرتبطة بحسابك", "error");
    store.reset();
    return renderAuth("login");
  }
  if (!store.memberships.some((m) => m.company.id === store.companyId)) {
    store.companyId = store.memberships[0].company.id;
  }
  try {
    await loadSettings();
  } catch {
    /* the header falls back to the membership name */
  }
  route();
}

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

store.load();
if (store.session) {
  bootstrap().catch(() => {
    store.reset();
    renderAuth("login");
  });
} else {
  renderAuth("login");
}
