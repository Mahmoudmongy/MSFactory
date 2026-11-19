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

/* Client mirror of the server's ROLE_PERMISSIONS (app/core/permissions.py), in
   the dot form the client uses. "*" means every permission. */
const CLIENT_PERMISSIONS = {
  owner: "*",
  admin: "*",
  accountant: new Set([
    "account.manage", "contact.view", "contact.manage", "item.view", "item.manage",
    "warehouse.manage", "stock.view", "stock.manage", "production.view", "production.manage",
    "invoice.view", "invoice.manage", "payment.view", "payment.manage", "journal.manage",
    "report.view", "period.manage", "audit.view",
  ]),
  sales: new Set([
    "contact.view", "contact.manage", "item.view", "stock.view", "invoice.view",
    "invoice.manage", "payment.view", "report.view",
  ]),
  storekeeper: new Set([
    "item.view", "item.manage", "warehouse.manage", "stock.view", "stock.manage",
    "production.view", "production.manage", "contact.view", "report.view",
  ]),
  viewer: new Set([
    "contact.view", "item.view", "stock.view", "production.view", "invoice.view",
    "payment.view", "report.view",
  ]),
  customer: new Set(),
};

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
    // Mirrors ROLE_PERMISSIONS on the server, in the dot form the client uses
    // ("invoice.manage", not "invoice:manage"). The server is still the
    // authority; this only hides controls a role can never use, so a rejected
    // click is not the first sign of a missing permission.
    const perms = CLIENT_PERMISSIONS[role];
    if (!perms) return false;
    return perms === "*" || perms.has(action);
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
  // Latin digits (1234, not ١٢٣٤) while the currency symbol stays Arabic.
  const formatted = new Intl.NumberFormat("en-US", {
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
  // Arabic month name, Latin digits: 18 نوفمبر 2026.
  return new Intl.DateTimeFormat("ar-EG-u-nu-latn", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(parsed);
}

export function today() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

/** Date and clock time, e.g. "18 نوفمبر 2026، 10:42 ص". */
export function dateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return new Intl.DateTimeFormat("ar-EG-u-nu-latn", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
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

// Document kinds that are returns (credit notes) rather than invoices. A return
// mirrors the invoice it reverses, so the UI treats it read-only: it is fixed
// by another return, not by editing or voiding it.
export const RETURN_KINDS = ["sales_return", "purchase_return"];

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

/* Sections shown in the sidebar, in the order the old app lists them.
   ``perm`` is the permission a role must hold for the link to appear; links
   without one are visible to everyone. The server enforces the same rules, so
   this only keeps the menu honest for the role in use. */
const NAV_SECTIONS = [
  { href: "#/dashboard", icon: "⌂", label: "الملخص" },
  { href: "#/accounts", icon: "▤", label: "الحسابات النقدية والبنوك" },
  { href: "#/contacts", icon: "♙", label: "العملاء والموردون" },
  { href: "#/payments", icon: "↙", label: "المقبوضات والمدفوعات" },
  { href: "#/invoices", icon: "▧", label: "الفواتير" },
  { href: "#/inventory", icon: "◇", label: "الأصناف والمخازن" },
  { href: "#/production", icon: "⚙", label: "أوامر الإنتاج" },
  { href: "#/ledger", icon: "☷", label: "شجرة الحسابات" },
  { href: "#/reports", icon: "▥", label: "التقارير" },
  { href: "#/statements", icon: "▧", label: "القوائم المالية" },
  { href: "#/journal", icon: "≡", label: "قيود اليومية" },
  { href: "#/team", icon: "♙", label: "الموظفون والصلاحيات", perm: "user.manage" },
  { href: "#/assistant", icon: "✦", label: "المساعد الذكي" },
  { href: "#/timeline", icon: "◷", label: "السجل الزمني", perm: "audit.view" },
  { href: "#/settings", icon: "⚙", label: "الإعدادات", perm: "settings.manage" },
];

function currentSegment() {
  return (location.hash || "#/dashboard").replace(/^#\//, "").split(/[/?]/)[0];
}

function sidebar() {
  const segment = currentSegment();
  const links = NAV_SECTIONS.filter((item) => !item.perm || store.can(item.perm)).map((item) => {
    const target = item.href.replace(/^#\//, "");
    const active = segment === target || (segment === "" && target === "dashboard");
    return `<a class="sidebar__link" href="${item.href}" ${active ? 'aria-current="page"' : ""}>
      <span class="sidebar__icon">${item.icon}</span><span>${escapeHtml(item.label)}</span></a>`;
  }).join("");
  return el(`<aside class="sidebar" aria-label="أقسام البرنامج">
    <div class="sidebar__brand"><span class="sidebar__mark">MS</span><span>MS-Factory</span></div>
    <nav class="sidebar__nav">${links}</nav>
    <div class="sidebar__footer">
      <div class="sidebar__user">${escapeHtml(store.user?.full_name || store.user?.email || "")}</div>
      <button class="sidebar__logout" type="button" data-logout>خروج</button>
    </div>
  </aside>`);
}

function toggleSidebar(open) {
  const node = app.querySelector(".sidebar");
  const overlay = app.querySelector(".sidebar-overlay");
  if (!node) return;
  const next = open ?? !node.classList.contains("is-open");
  node.classList.toggle("is-open", next);
  overlay?.classList.toggle("is-open", next);
}

function setScreen(content, { appbar: topbar, tabbar: bottomNav, plain } = {}) {
  app.innerHTML = "";
  // Auth screens are a single centred column: no sidebar, no drawer, no nav.
  if (!plain) app.appendChild(sidebar());
  const column = el(`<div class="content"></div>`);
  if (topbar) column.appendChild(topbar);
  const screen = el(`<main class="screen${plain ? " screen--plain" : ""}"></main>`);
  if (typeof content === "string") screen.innerHTML = content;
  else screen.appendChild(content);
  column.appendChild(screen);
  app.appendChild(column);
  if (!plain) {
    app.appendChild(el(`<div class="sidebar-overlay" data-close-sidebar></div>`));
    if (bottomNav) app.appendChild(bottomNav);
  }
}

function appbar({ title, subtitle, back, actions = "" }) {
  const dark = document.documentElement.dataset.theme === "dark";
  return el(`<header class="appbar">
    <button class="icon-btn appbar__menu" data-toggle-sidebar aria-label="القائمة">☰</button>
    ${back ? `<button class="icon-btn" data-nav="${escapeHtml(back)}" aria-label="رجوع">→</button>` : ""}
    <h1 class="appbar__title">${escapeHtml(title)}
      ${subtitle ? `<span class="appbar__sub">${escapeHtml(subtitle)}</span>` : ""}</h1>
    ${actions}
    <button class="icon-btn" data-open-theme aria-label="ألوان البرنامج">🎨</button>
    <button class="icon-btn" data-toggle-dark aria-label="تبديل الوضع الليلي">${dark ? "☀" : "☾"}</button>
  </header>`);
}

/** Small popover for picking the accent colour, mirroring the old app's picker. */
function openThemePicker() {
  const current = JSON.parse(localStorage.getItem("msfactory.theme") || "null")?.name || "teal";
  const swatches = Object.entries(THEME_PRESETS)
    .map(
      ([name, preset]) => `<button type="button" class="theme-choice" data-theme-choice="${name}"
        aria-pressed="${name === current}"><i class="theme-swatch" style="--swatch:${preset.color}"></i>${THEME_LABELS[name]}</button>`
    )
    .join("");
  const popover = el(`<div class="theme-popover" role="dialog" aria-label="ألوان البرنامج">
    <div class="theme-popover__head"><strong>ألوان البرنامج</strong>
      <button class="icon-btn" type="button" data-close-theme aria-label="إغلاق">✕</button></div>
    <div class="theme-options">${swatches}</div>
    <label class="theme-custom">لون مخصص
      <input type="color" id="custom-theme" value="${THEME_PRESETS[current].color}" /></label>
  </div>`);
  const close = () => popover.remove();
  popover.querySelector("[data-close-theme]").onclick = close;
  popover.querySelectorAll("[data-theme-choice]").forEach((button) => {
    button.onclick = () => {
      applyTheme(button.dataset.themeChoice);
      popover.querySelectorAll("[data-theme-choice]").forEach((b) => b.setAttribute("aria-pressed", String(b === button)));
    };
  });
  popover.querySelector("#custom-theme").oninput = (event) => applyTheme("custom", event.target.value);
  document.body.appendChild(popover);
  setTimeout(() => {
    const away = (event) => {
      if (!popover.contains(event.target)) {
        close();
        document.removeEventListener("click", away, true);
      }
    };
    document.addEventListener("click", away, true);
  }, 0);
}

const THEME_LABELS = { teal: "تركواز", blue: "أزرق هادئ", green: "أخضر", purple: "بنفسجي", clay: "طوبي" };

/* Theme: an accent colour plus a light/dark mode, both remembered per browser.
   The accent only rewrites the brand tokens, so every component follows. */
const THEME_PRESETS = {
  teal: { color: "#0f766e", sidebar: "#1f2d3a" },
  blue: { color: "#315b8a", sidebar: "#1d2b3f" },
  green: { color: "#19764b", sidebar: "#20362c" },
  purple: { color: "#7650a1", sidebar: "#30253b" },
  clay: { color: "#b65a3a", sidebar: "#392923" },
};

function shadeBrand(hex) {
  // Derive the lighter/darker brand steps from one accent so the palette stays coherent.
  const c = hex.replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(c)) return;
  const mix = (i, factor) => {
    const v = parseInt(c.slice(i, i + 2), 16);
    return Math.round(factor > 1 ? v + (255 - v) * (factor - 1) : v * factor)
      .toString(16)
      .padStart(2, "0");
  };
  const root = document.documentElement.style;
  root.setProperty("--brand-700", hex);
  root.setProperty("--brand-600", `#${mix(0, 1.12)}${mix(2, 1.12)}${mix(4, 1.12)}`);
  root.setProperty("--brand-500", `#${mix(0, 1.3)}${mix(2, 1.3)}${mix(4, 1.3)}`);
  root.setProperty("--brand-200", `#${mix(0, 1.75)}${mix(2, 1.75)}${mix(4, 1.75)}`);
  root.setProperty("--brand-100", `#${mix(0, 1.9)}${mix(2, 1.9)}${mix(4, 1.9)}`);
  root.setProperty("--brand-50", `#${mix(0, 1.96)}${mix(2, 1.96)}${mix(4, 1.96)}`);
}

function applyTheme(name, color) {
  const preset = THEME_PRESETS[name];
  const accent = color || preset?.color || THEME_PRESETS.teal.color;
  shadeBrand(accent);
  document.documentElement.style.setProperty("--sidebar-bg", preset?.sidebar || "#1f2d3a");
  try {
    localStorage.setItem("msfactory.theme", JSON.stringify({ name, color: accent }));
  } catch {
    /* storage may be blocked in private mode; the theme still applies this session */
  }
}

function applyDarkMode(dark) {
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try {
    localStorage.setItem("msfactory.dark", dark ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function loadTheme() {
  try {
    const saved = JSON.parse(localStorage.getItem("msfactory.theme") || "null");
    if (saved) applyTheme(saved.name, saved.color);
    applyDarkMode(localStorage.getItem("msfactory.dark") === "1");
  } catch {
    /* ignore */
  }
}

const TABS = [
  { href: "#/dashboard", icon: "🏠", label: "الملخص" },
  { href: "#/invoices", icon: "🧾", label: "الفواتير" },
  { href: "#/payments", icon: "💵", label: "المقبوضات والمدفوعات" },
  { href: "#/production", icon: "🏭", label: "أوامر الإنتاج" },
  { href: "#/contacts", icon: "👥", label: "العملاء والموردون" },
  { href: "#/reports", icon: "📊", label: "التقارير" },
];

function tabbar() {
  const current = location.hash || "#/dashboard";
  return el(`<nav class="tabbar">${TABS.map(
    (tab) => `<a class="tabbar__item" href="${tab.href}" ${current.startsWith(tab.href) ? 'aria-current="page"' : ""}>
        <span class="tabbar__icon">${tab.icon}</span><span>${tab.label}</span></a>`
  ).join("")}</nav>`);
}

/* Sub-tabs inside a section (sales vs purchases, customers vs suppliers…). The
   active tab is read from the hash query so a tab is linkable and survives a
   reload; clicking one rewrites the query and re-routes. */
function subTabs(group, tabs, active) {
  return `<div class="subtabs">${tabs
    .map(
      (tab) => `<button class="subtabs__btn" type="button" data-subtab="${group}:${tab.key}"
        aria-pressed="${tab.key === active}" ${tab.disabled ? "disabled" : ""}>${escapeHtml(tab.label)}</button>`
    )
    .join("")}</div>`;
}

/** The current value of a hash query parameter, e.g. #/invoices?view=purchases. */
function hashParam(name, fallback = "") {
  const query = (location.hash.split("?")[1] || "");
  return new URLSearchParams(query).get(name) || fallback;
}

// ---------------------------------------------------------------- auth screens

/** The sign-in screen. Accounts are created by an administrator from the team
 * screen, so there is no self-service registration here. */
function renderAuth() {
  const view = el(`<div class="auth screen--plain">
    <div class="auth__logo">
      <div class="auth__mark">MS</div>
      <h1 class="auth__title">MS-Factory</h1>
      <p class="auth__sub">نظام إدارة المحاسبة والمخازن</p>
    </div>
    <form novalidate>
      <div class="field">
        <label class="field__label" for="email">البريد الإلكتروني</label>
        <input class="input" id="email" name="email" type="email" inputmode="email" dir="ltr"
          autocomplete="username" required placeholder="you@company.com" />
      </div>
      <div class="field">
        <label class="field__label" for="password">كلمة المرور</label>
        <input class="input" id="password" name="password" type="password" dir="ltr"
          autocomplete="current-password" required placeholder="••••••••" />
      </div>
      <button class="btn btn--block" type="submit">تسجيل الدخول</button>
    </form>
    <p class="auth__switch">للحصول على بيانات الدخول، تواصل مع مسؤول الشركة.</p>
  </div>`);

  view.querySelector("form").onsubmit = async (event) => {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector("button[type=submit]");
    const data = Object.fromEntries(new FormData(form));
    button.disabled = true;
    button.textContent = "جارٍ...";
    try {
      store.save(await api("/auth/login", { method: "POST", body: { email: data.email, password: data.password } }));
      await bootstrap();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
      button.textContent = "تسجيل الدخول";
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
  setScreen(spinner(), { appbar: appbar({ title: "الفواتير والمرتجعات" }), tabbar: tabbar() });
  try {
    const invoices = await api("/invoices");
    await loadContacts();

    const view = hashParam("invoices", "sales");
    const KIND_LABELS = {
      sales: "فواتير البيع",
      purchase: "فواتير الشراء",
      sales_return: "مرتجعات البيع",
      purchase_return: "مرتجعات الشراء",
    };
    const shown = invoices.filter((i) => i.kind === view);
    const rows = shown
      .map(
        (invoice) => `<li><a class="list__item" href="#/invoices/${invoice.id}">
        <div class="list__main">
          <div class="list__title">${escapeHtml(contactName(invoice.contact_id))}</div>
          <div class="list__meta">${escapeHtml(invoice.number)} · ${date(invoice.issue_date)}</div>
        </div>
        <div style="text-align:end">
          <div class="list__value num">${money(invoice.total, invoice.currency)}</div>
          <div style="margin-top:4px">${statusBadge(invoice.status)}</div>
        </div></a></li>`
      )
      .join("");

    setScreen(
      el(`<div>
        ${subTabs(
          "invoices",
          [
            { key: "sales", label: "فواتير البيع" },
            { key: "sales_return", label: "مرتجعات البيع" },
            { key: "purchase", label: "فواتير الشراء" },
            { key: "purchase_return", label: "مرتجعات الشراء" },
          ],
          view
        )}
        <div class="card card--flush">
          ${shown.length ? `<ul class="list">${rows}</ul>` : emptyState("🧾", "لا توجد مستندات", `${KIND_LABELS[view]} ستظهر هنا`)}
        </div>
        <div class="action-bar"><a class="btn" href="#/invoices/new">＋ فاتورة جديدة</a></div>
      </div>`),
      { appbar: appbar({ title: "الفواتير والمرتجعات" }), tabbar: tabbar() }
    );
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
}

/** Bottom sheet to pick returnable lines off an invoice and return them. */
async function openReturnSheet(invoiceId, onDone) {
  let close = () => {};
  let lines = [];
  try {
    lines = await api(`/invoices/${invoiceId}/returnable`);
  } catch (error) {
    toast(error.message, "error");
    return;
  }
  const open = lines.filter((l) => Number(l.remaining) > 0);
  if (!open.length) {
    toast("لا يوجد بند متبقٍ للإرجاع", "error");
    return;
  }

  const form = el(`<form novalidate>
    <div class="field"><label class="field__label" for="rdate">تاريخ المرتجع</label>
      <input class="input" type="date" id="rdate" value="${today()}" required /></div>
    <div class="section-title">البنود المتاحة للإرجاع</div>
    <div class="card card--flush">
      <ul class="list">${open
        .map(
          (l) => `<li class="list__item" style="cursor:default">
          <div class="list__main">
            <div class="list__title">${escapeHtml(l.description || "—")}</div>
            <div class="list__meta">متاح ${Number(l.remaining)} من ${Number(l.quantity)} · ${money(l.unit_price)}</div>
          </div>
          <input class="input num" style="max-width:96px" type="number" inputmode="decimal"
            min="0" max="${Number(l.remaining)}" step="0.01" value="0"
            data-return-qty="${l.line_id}" aria-label="الكمية المرتجعة" /></li>`
        )
        .join("")}</ul>
    </div>
    <div class="field"><label class="field__label" for="rnotes">ملاحظات</label>
      <textarea class="textarea" id="rnotes" placeholder="اختياري"></textarea></div>
    <div class="action-bar">
      <button class="btn" type="submit">حفظ المرتجع</button>
      <button class="btn btn--ghost" type="button" data-cancel>إلغاء</button>
    </div>
  </form>`);

  form.querySelector("[data-cancel]").onclick = () => close();
  form.onsubmit = async (event) => {
    event.preventDefault();
    const payload = {
      issue_date: form.querySelector("#rdate").value,
      notes: form.querySelector("#rnotes").value,
      lines: [...form.querySelectorAll("[data-return-qty]")]
        .map((input) => ({ line_id: Number(input.dataset.returnQty), quantity: Number(input.value) }))
        .filter((l) => l.quantity > 0),
    };
    if (!payload.lines.length) {
      toast("أدخل كمية مرتجعة لبند واحد على الأقل", "error");
      return;
    }
    try {
      await api(`/invoices/${invoiceId}/returns`, { method: "POST", body: payload });
      toast("تم إنشاء المرتجع", "success");
      close();
      onDone?.();
    } catch (error) {
      toast(error.message, "error");
    }
  };

  close = openSheet("مرتجع جديد", form);
}

async function renderInvoiceForm({ invoiceId } = {}) {
  const editing = Boolean(invoiceId);
  let existing = null;
  if (editing) {
    existing = await api(`/invoices/${invoiceId}`);
  }
  const [accounts, contacts] = await Promise.all([loadAccounts(), loadContacts()]);
  const customers = contacts.filter((c) => c.kind === "customer");
  let items = [];
  let warehouses = [];
  try {
    const options = await loadInventoryOptions();
    items = options.items;
    warehouses = options.warehouses;
  } catch {
    items = [];
    warehouses = [];
  }

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

  function addLine(prefill = null) {
    const node = el(`<div class="line-editor">
      <div class="line-editor__head"><span>بند</span>
        <button type="button" class="btn btn--ghost btn--sm" data-remove>حذف</button></div>
      <div class="field"><label class="field__label">الوصف</label>
        <input class="input" data-description placeholder="وصف البند" value="${escapeHtml(prefill?.description || "")}" /></div>
      ${items.length
        ? `<div class="line-editor__row">
        <div class="field"><label class="field__label">الصنف (يحرّك المخزون)</label>
          <select class="select" data-item>
            <option value="">— بدون صنف (خدمة) —</option>
            ${items
              .map(
                (i) =>
                  `<option value="${i.id}" ${prefill?.item_id === i.id ? "selected" : ""}>${escapeHtml(i.name)}</option>`
              )
              .join("")}
          </select></div>
        <div class="field"><label class="field__label">المخزن</label>
          <select class="select" data-warehouse>
            ${warehouses
              .map(
                (w) =>
                  `<option value="${w.id}" ${
                    prefill?.warehouse_id ? (prefill.warehouse_id === w.id ? "selected" : "") : w.code === "MAIN" ? "selected" : ""
                  }>${escapeHtml(w.name_ar || w.name)}</option>`
              )
              .join("")}
          </select></div>
      </div>`
        : ""}
      <div class="line-editor__row">
        <div class="field"><label class="field__label">الكمية</label>
          <input class="input num" data-quantity type="number" step="0.01" min="0" value="${prefill ? Number(prefill.quantity) : 1}" inputmode="decimal" /></div>
        <div class="field"><label class="field__label">سعر الوحدة</label>
          <input class="input num" data-unit-price type="number" step="0.01" min="0" value="${prefill ? Number(prefill.unit_price) : 0}" inputmode="decimal" /></div>
      </div>
      <div class="line-editor__row">
        <div class="field"><label class="field__label">خصم %</label>
          <input class="input num" data-discount type="number" step="0.01" min="0" max="100" value="${prefill ? Number(prefill.discount_rate) : 0}" inputmode="decimal" /></div>
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
    const itemSelect = node.querySelector("[data-item]");
    if (itemSelect) {
      itemSelect.addEventListener("change", () => {
        const chosen = items.find((i) => i.id === Number(itemSelect.value));
        const priceField = node.querySelector("[data-unit-price]");
        const price = kind === "purchase" ? chosen?.purchase_price : chosen?.sale_price;
        if (chosen && price && Number(price) > 0 && Number(priceField.value) === 0) {
          priceField.value = Number(price);
        }
        recalc();
      });
    }
    linesHost.appendChild(node);
    if (!prefill) node.querySelector("[data-description]").focus();
  }

  function collect() {
    return [...linesHost.querySelectorAll(".line-editor")].map((node) => {
      const itemId = node.querySelector("[data-item]")?.value || "";
      const warehouseId = node.querySelector("[data-warehouse]")?.value || "";
      return {
        description: node.querySelector("[data-description]").value.trim(),
        quantity: Number(node.querySelector("[data-quantity]").value) || 0,
        unit_price: Number(node.querySelector("[data-unit-price]").value) || 0,
        discount_rate: Number(node.querySelector("[data-discount]").value) || 0,
        tax_rate: Number(node.querySelector("[data-tax-rate]").value) || 0,
        // Naming an item is what moves stock; leaving it blank keeps the line
        // as free text that touches no inventory.
        ...(itemId ? { item_id: Number(itemId) } : {}),
        ...(itemId && warehouseId ? { warehouse_id: Number(warehouseId) } : {}),
      };
    });
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

  view.querySelector("[data-add-line]").onclick = () => addLine();
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
      const invoice = editing
        ? await api(`/invoices/${invoiceId}`, { method: "PATCH", body: payload })
        : await api("/invoices", {
            method: "POST",
            body: payload,
            headers: { "Idempotency-Key": idempotencyKey("inv", payload) },
          });
      toast(editing ? "تم تحديث الفاتورة" : `تم حفظ الفاتورة ${invoice.number}`, "success");
      location.hash = `#/invoices/${invoice.id}`;
    } catch (error) {
      toast(error.message, "error");
      submit.disabled = false;
      submit.textContent = editing ? "حفظ التعديلات" : "حفظ الفاتورة";
    }
  };

  if (existing) {
    kind = existing.kind;
    view.querySelectorAll("[data-kind]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.kind === kind))
    );
    view.querySelector("#contact").value = String(existing.contact_id);
    view.querySelector("#issue_date").value = String(existing.issue_date).slice(0, 10);
    view.querySelector("#due_date").value = existing.due_date ? String(existing.due_date).slice(0, 10) : "";
    view.querySelector("#notes").value = existing.notes || "";
    existing.lines.forEach((line) => addLine(line));
  } else {
    addLine();
  }
  recalc();
  setScreen(view, {
    appbar: appbar({ title: editing ? "تعديل الفاتورة" : "فاتورة جديدة", back: "#/invoices" }),
  });
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
            <div class="list__meta">${escapeHtml(contactName(invoice.contact_id))}</div>
            ${RETURN_KINDS.includes(invoice.kind)
              ? `<div class="list__meta">${invoice.kind === "sales_return" ? "مرتجع بيع" : "مرتجع شراء"}</div>`
              : ""}</div>
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

      <div id="returns-block"></div>

      <div class="row-actions">
        ${invoice.status !== "void" && outstanding > 0 && store.can("payment.manage")
          ? '<button class="btn" data-pay>💵 تسجيل دفعة</button>' : ""}
        ${!RETURN_KINDS.includes(invoice.kind) && ["issued", "partially_paid", "paid"].includes(invoice.status) && store.can("invoice.manage")
          ? '<button class="btn btn--ghost" data-return>↩ مرتجع</button>' : ""}
        ${invoice.status !== "void" && Number(invoice.amount_paid) === 0 && !RETURN_KINDS.includes(invoice.kind) && store.can("invoice.manage")
          ? '<button class="btn btn--ghost" data-edit>✎ تعديل الفاتورة</button>' : ""}
        ${invoice.status !== "void" && !RETURN_KINDS.includes(invoice.kind) && store.can("invoice.manage")
          ? '<button class="btn btn--danger" data-void>إلغاء الفاتورة</button>' : ""}
      </div>
    </div>`);

    view.querySelector("[data-edit]")?.addEventListener("click", () => {
      location.hash = `#/invoices/${invoiceId}/edit`;
    });

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

    const returnButton = view.querySelector("[data-return]");
    if (returnButton) {
      returnButton.onclick = () => openReturnSheet(invoiceId, () => renderInvoiceDetail(invoiceId));
    }

    // A return is a separate document pointing back here; showing them under
    // the invoice is what makes "how much of this was given back" answerable.
    const returnsBlock = view.querySelector("#returns-block");
    if (returnsBlock && !RETURN_KINDS.includes(invoice.kind)) {
      api(`/invoices/${invoiceId}/returns`)
        .then((returns) => {
          if (!returns.length) return;
          const total = returns.reduce((sum, r) => sum + Number(r.total), 0);
          returnsBlock.innerHTML = `<div class="section-title">المرتجعات</div>
            <div class="card card--flush">
              <ul class="list">${returns
                .map(
                  (r) => `<li><a class="list__item" href="#/invoices/${r.id}">
                <div class="list__main">
                  <div class="list__title">${escapeHtml(r.number)}</div>
                  <div class="list__meta">${date(r.issue_date)}</div>
                </div>
                <div class="list__value num">${money(r.total)}</div></a></li>`
                )
                .join("")}</ul>
              <div class="totals"><div class="totals__row totals__row--grand">
                <span>إجمالي المرتجعات</span><span class="num">${money(total)}</span></div></div>
            </div>`;
        })
        .catch(() => {});
    }

    setScreen(view, { appbar: appbar({ title: invoice.number, back: "#/invoices" }) });
  } catch (error) {
    setScreen(emptyState("⚠️", "تعذّر تحميل الفاتورة", error.message), { tabbar: tabbar() });
  }
}

// ---------------------------------------------------------------- production

const WORK_ORDER_LABELS = {
  draft: "مسودة",
  in_progress: "قيد التنفيذ",
  completed: "مكتمل",
  cancelled: "ملغى",
};
const workOrderLabel = (status) => WORK_ORDER_LABELS[status] || status;

async function loadInventoryOptions() {
  const [items, warehouses] = await Promise.all([
    api("/inventory/items"),
    api("/inventory/warehouses"),
  ]);
  return {
    items: items.filter((i) => i.track_inventory),
    warehouses: warehouses.filter((w) => !w.is_group),
  };
}

async function renderProduction() {
  setScreen(spinner(), { appbar: appbar({ title: "أوامر الإنتاج" }), tabbar: tabbar() });
  try {
    const orders = await api("/production/work-orders");
    const rows = orders
      .map(
        (order) => `<li><a class="list__item" href="#/production/${order.id}">
        <div class="list__main">
          <div class="list__title">${escapeHtml(order.number)}</div>
          <div class="list__meta">الكمية ${Number(order.quantity)} · ${date(order.start_date)}</div>
        </div>
        <div style="text-align:end">
          <div class="list__value num">${money(order.material_cost)}</div>
          <div style="margin-top:4px"><span class="badge">${escapeHtml(workOrderLabel(order.status))}</span></div>
        </div></a></li>`
      )
      .join("");

    setScreen(
      el(`<div>
        <div class="card card--flush">
          ${orders.length ? `<ul class="list">${rows}</ul>` : emptyState("🏭", "لا توجد أوامر إنتاج", "أنشئ أمر إنتاج لتحويل الخامات إلى منتج تام")}
        </div>
        <div class="action-bar"><a class="btn" href="#/production/new">＋ أمر إنتاج</a></div>
      </div>`),
      { appbar: appbar({ title: "أوامر الإنتاج" }), tabbar: tabbar() }
    );
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
}

async function renderWorkOrderForm() {
  setScreen(spinner(), { appbar: appbar({ title: "أمر إنتاج", back: "#/production" }) });
  try {
    const { items, warehouses } = await loadInventoryOptions();
    const manufactured = items.filter((i) => ["manufactured", "both"].includes(i.item_type));

    const view = el(`<form novalidate>
      <div class="card">
        <div class="field"><label class="field__label" for="item">المنتج التام</label>
          <select class="select" id="item" required>
            <option value="">— اختر —</option>
            ${manufactured
              .map((i) => `<option value="${i.id}">${escapeHtml(i.name)}</option>`)
              .join("")}
          </select>
          ${manufactured.length ? "" : '<div class="field__hint">لا يوجد منتج نوعه "مصنّع" — أضفه من المخزون أولاً</div>'}
        </div>
        <div class="line-editor__row">
          <div class="field"><label class="field__label" for="quantity">الكمية المطلوبة</label>
            <input class="input num" id="quantity" type="number" step="0.01" min="0.01" value="1" inputmode="decimal" required /></div>
          <div class="field"><label class="field__label" for="warehouse">مخزن الاستلام</label>
            <select class="select" id="warehouse">
              ${warehouses
                .map((w) => `<option value="${w.id}" ${w.code === "FG" ? "selected" : ""}>${escapeHtml(w.name_ar || w.name)}</option>`)
                .join("")}
            </select></div>
        </div>
        <div class="line-editor__row">
          <div class="field"><label class="field__label" for="start_date">تاريخ البدء</label>
            <input class="input" type="date" id="start_date" value="${today()}" required /></div>
          <div class="field"><label class="field__label" for="due_date">تاريخ الاستحقاق</label>
            <input class="input" type="date" id="due_date" /></div>
        </div>
      </div>

      <div class="section-title">الخامات المستخدمة</div>
      <div id="materials"></div>
      <button type="button" class="btn btn--ghost btn--block" data-add-material>＋ إضافة خامة</button>

      <div class="field" style="margin-top:12px"><label class="field__label" for="notes">ملاحظات</label>
        <textarea class="textarea" id="notes" placeholder="اختياري"></textarea></div>

      <div class="action-bar">
        <button class="btn" type="submit">حفظ أمر الإنتاج</button>
        <button class="btn btn--ghost" type="button" data-nav="#/production">إلغاء</button>
      </div>
    </form>`);

    const host = view.querySelector("#materials");
    function addMaterial() {
      const node = el(`<div class="line-editor">
        <div class="line-editor__head"><span>خامة</span>
          <button type="button" class="btn btn--ghost btn--sm" data-remove>حذف</button></div>
        <div class="line-editor__row">
          <div class="field"><label class="field__label">الصنف</label>
            <select class="select" data-material-item>
              ${items.map((i) => `<option value="${i.id}">${escapeHtml(i.name)}</option>`).join("")}
            </select></div>
          <div class="field"><label class="field__label">الكمية</label>
            <input class="input num" data-material-qty type="number" step="0.01" min="0" value="1" inputmode="decimal" /></div>
        </div>
      </div>`);
      node.querySelector("[data-remove]").onclick = () => node.remove();
      host.appendChild(node);
    }
    view.querySelector("[data-add-material]").onclick = addMaterial;

    view.onsubmit = async (event) => {
      event.preventDefault();
      const submit = view.querySelector("button[type=submit]");
      const itemId = Number(view.querySelector("#item").value);
      const materials = [...host.querySelectorAll(".line-editor")].map((node) => ({
        item_id: Number(node.querySelector("[data-material-item]").value),
        quantity: Number(node.querySelector("[data-material-qty]").value) || 0,
      }));
      if (!itemId) return toast("اختر المنتج التام", "error");
      if (!materials.length) return toast("أضف خامة واحدة على الأقل", "error");

      const payload = {
        item_id: itemId,
        quantity: Number(view.querySelector("#quantity").value) || 0,
        start_date: view.querySelector("#start_date").value,
        due_date: view.querySelector("#due_date").value || null,
        warehouse_id: Number(view.querySelector("#warehouse").value) || null,
        notes: view.querySelector("#notes").value.trim(),
        materials,
      };
      submit.disabled = true;
      submit.textContent = "جارٍ الحفظ...";
      try {
        const order = await api("/production/work-orders", {
          method: "POST",
          body: payload,
          headers: { "Idempotency-Key": idempotencyKey("wo", payload) },
        });
        toast(`تم إنشاء الأمر ${order.number}`, "success");
        location.hash = `#/production/${order.id}`;
      } catch (error) {
        toast(error.message, "error");
        submit.disabled = false;
        submit.textContent = "حفظ أمر الإنتاج";
      }
    };

    addMaterial();
    setScreen(view, { appbar: appbar({ title: "أمر إنتاج جديد", back: "#/production" }) });
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
}

async function renderWorkOrderDetail(orderId) {
  setScreen(spinner(), { appbar: appbar({ title: "أمر إنتاج", back: "#/production" }) });
  try {
    const order = await api(`/production/work-orders/${orderId}`);
    const { items } = await loadInventoryOptions();
    const name = (id) => items.find((i) => i.id === id)?.name || `#${id}`;

    const view = el(`<div>
      <div class="card">
        <div class="card__head">
          <div><h2 class="card__title">${escapeHtml(order.number)}</h2>
            <div class="list__meta">${escapeHtml(name(order.item_id))}</div></div>
          <span class="badge">${escapeHtml(workOrderLabel(order.status))}</span>
        </div>
        <div class="totals__row"><span>الكمية المطلوبة</span><span class="num">${Number(order.quantity)}</span></div>
        <div class="totals__row"><span>الكمية المنتجة</span><span class="num">${Number(order.produced_quantity)}</span></div>
        <div class="totals__row"><span>تكلفة الخامات</span><span class="num">${money(order.material_cost)}</span></div>
        <div class="totals__row totals__row--grand"><span>تكلفة الوحدة</span><span class="num">${money(order.unit_cost)}</span></div>
      </div>

      <div class="section-title">الخامات</div>
      <div class="card card--flush table-wrap">
        <table>
          <thead><tr><th>الصنف</th><th class="num">الكمية</th><th class="num">التكلفة</th></tr></thead>
          <tbody>${order.materials
            .map(
              (m) => `<tr><td>${escapeHtml(name(m.item_id))}</td>
                <td class="num">${Number(m.quantity)}</td>
                <td class="num">${money(m.cost_amount)}</td></tr>`
            )
            .join("")}</tbody>
        </table>
      </div>

      ${order.notes ? `<div class="card"><div class="card__title">ملاحظات</div>
        <p style="margin:8px 0 0;color:var(--ink-700)">${escapeHtml(order.notes)}</p></div>` : ""}

      <div class="row-actions">
        ${order.status === "draft" && store.can("production.manage")
          ? '<button class="btn" data-start>▶ بدء التنفيذ</button>' : ""}
        ${["draft", "in_progress"].includes(order.status) && store.can("production.manage")
          ? '<button class="btn" data-complete>✔ إتمام الإنتاج</button>' : ""}
        ${["draft", "in_progress"].includes(order.status) && store.can("production.manage")
          ? '<button class="btn btn--danger" data-cancel>إلغاء الأمر</button>' : ""}
      </div>
    </div>`);

    const act = async (path, body, message) => {
      try {
        await api(`/production/work-orders/${orderId}/${path}`, { method: "POST", body });
        toast(message, "success");
        renderWorkOrderDetail(orderId);
      } catch (error) {
        toast(error.message, "error");
      }
    };
    view.querySelector("[data-start]")?.addEventListener("click", () => act("start", {}, "بدأ التنفيذ"));
    view.querySelector("[data-complete]")?.addEventListener("click", () =>
      act("complete", { produced_quantity: Number(order.quantity) }, "تم إتمام الإنتاج")
    );
    view.querySelector("[data-cancel]")?.addEventListener("click", async () => {
      if (await confirmDialog("سيتم إرجاع الخامات إلى المخزن وإلغاء الأمر. متابعة؟")) {
        act("cancel", { reason: "إلغاء من التطبيق" }, "تم إلغاء الأمر");
      }
    });

    setScreen(view, { appbar: appbar({ title: order.number, back: "#/production" }) });
  } catch (error) {
    setScreen(emptyState("⚠️", "تعذّر تحميل الأمر", error.message), { tabbar: tabbar() });
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
  setScreen(spinner(), { appbar: appbar({ title: "المقبوضات والمدفوعات" }), tabbar: tabbar() });
  try {
    const [payments, accounts] = await Promise.all([api("/payments"), loadAccounts()]);
    await loadContacts();
    const cashAccounts = accounts.filter((a) => ["1010", "1020"].includes(a.code));

    const kind = hashParam("payments", "receipts");
    const isPaymentsTab = kind === "payments";
    const shown = payments.filter((p) => (isPaymentsTab ? p.kind === "spend" : p.kind === "receive"));

    const rows = shown
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
        ${subTabs(
          "payments",
          [
            { key: "receipts", label: "سندات القبض" },
            { key: "payments", label: "سندات الدفع" },
          ],
          kind
        )}
        <div class="card card--flush">
          ${shown.length
            ? `<ul class="list">${rows}</ul>`
            : emptyState("💵", isPaymentsTab ? "لا توجد سندات دفع" : "لا توجد سندات قبض", "سجّل تحصيلاً من عميل أو دفعة لمورد")}
        </div>
        <div class="action-bar">
          <button class="btn" data-new="receive">＋ سند قبض</button>
          <button class="btn btn--ghost" data-new="spend">＋ سند دفع</button>
        </div>
      </div>`),
      { appbar: appbar({ title: "المقبوضات والمدفوعات" }), tabbar: tabbar() }
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
    // One record can be both a customer and a supplier; the old app shows it in
    // both tabs, so filter by "is a customer" / "is a supplier" rather than by a
    // single exclusive kind.
    const kind = hashParam("contacts", "customers");
    const isSupplierTab = kind === "suppliers";
    const shown = contacts.filter((c) =>
      isSupplierTab ? c.kind === "supplier" || c.kind === "both" : c.kind === "customer" || c.kind === "both"
    );
    const rows = shown
      .map(
        (contact) => `<li><a class="list__item" href="#/contacts/${contact.id}">
        <div class="list__main">
          <div class="list__title">${escapeHtml(contact.name)}</div>
          <div class="list__meta">${contact.kind === "supplier" ? "مورد" : contact.kind === "both" ? "عميل ومورد" : "عميل"}
            ${contact.phone ? `· <span class="num">${escapeHtml(contact.phone)}</span>` : ""}</div>
        </div><span style="color:var(--ink-400)">‹</span></a></li>`
      )
      .join("");

    setScreen(
      el(`<div>
        ${subTabs(
          "contacts",
          [
            { key: "customers", label: "العملاء" },
            { key: "suppliers", label: "الموردون" },
          ],
          kind
        )}
        <div class="card card--flush">
          ${shown.length
            ? `<ul class="list">${rows}</ul>`
            : emptyState("👥", isSupplierTab ? "لا يوجد موردون" : "لا يوجد عملاء", "أضف عميلاً أو مورداً للبدء")}
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

/* The report catalogue comes from the API, so this menu cannot drift from the
 * endpoints that exist: a report is listed because the server says it is there.
 * Each report declares the filters it accepts, and one generic renderer shows
 * whatever shape the report returns — a block of totals, tables of rows, or
 * both — so a report added on the server needs no new code here.
 *
 * Every report can be downloaded as a real .xlsx, built server-side from the
 * same figures on screen. */

const COLUMN_LABELS = {
  code: "الكود", name: "الاسم", name_ar: "الاسم", account: "الحساب", account_id: "الحساب",
  type: "النوع", debit: "مدين", credit: "دائن", balance: "الرصيد", amount: "المبلغ",
  total: "الإجمالي", date: "التاريخ", entry_date: "التاريخ", issue_date: "تاريخ الفاتورة",
  due_date: "تاريخ الاستحقاق", reference: "المرجع", memo: "البيان", number: "الرقم",
  description: "الوصف", quantity: "الكمية", unit_price: "السعر", unit_cost: "التكلفة",
  cost: "التكلفة", revenue: "الإيراد", profit: "الربح", margin_percent: "الهامش %",
  share_percent: "النسبة %", tax: "الضريبة", net: "الصافي", gross: "الإجمالي",
  discount: "الخصم", contact: "الجهة", contact_id: "الجهة", customer: "العميل",
  supplier: "المورد", item: "الصنف", item_id: "الصنف", warehouse: "المخزن",
  status: "الحالة", invoice_count: "عدد الفواتير", count: "العدد", outstanding: "المتبقي",
  paid: "المدفوع", opening: "الافتتاحي", closing: "الختامي", running_balance: "الرصيد",
  balance_before: "الرصيد قبل", age_days: "العمر (يوم)", days: "الأيام", bucket: "الفئة",
  current: "جاري", value: "القيمة", quantity_remaining: "الكمية المتبقية",
  quantity_received: "الكمية المستلمة", days_overdue: "أيام التأخير", limit: "الحد الأقصى",
  month: "الشهر", year: "السنة", shortfall: "النقص", reorder_level: "حد الطلب",
  party: "الجهة", kind: "النوع", source: "المصدر",
  available: "المتاح", on_hand: "المتاح", tax_total: "إجمالي الضريبة", tax_rate: "نسبة الضريبة",
  cost_of_sales: "تكلفة المبيعات", gross_profit: "الربح الإجمالي", net_profit: "صافي الربح",
  total_revenue: "إجمالي الإيرادات", total_expenses: "إجمالي المصروفات",
  total_cost_of_sales: "إجمالي تكلفة المبيعات", total_assets: "إجمالي الأصول",
  total_liabilities: "إجمالي الالتزامات", total_equity: "حقوق الملكية",
  total_debit: "إجمالي المدين", total_credit: "إجمالي الدائن", total_value: "إجمالي القيمة",
  total_quantity: "إجمالي الكمية", total_in: "إجمالي الوارد", total_out: "إجمالي المنصرف",
  receivables: "المستحق لك", payables: "المستحق عليك", inventory_value: "قيمة المخزون",
  cash: "النقدية", output_tax: "ضريبة المخرجات", input_tax: "ضريبة المدخلات",
  net_payable: "صافي المستحق", overdue_count: "عدد المتأخرات", overdue_total: "إجمالي المتأخرات",
  balanced: "متوازن", costing_method: "طريقة التكلفة", periods: "الفترات",
  cost_of_goods_sold: "تكلفة المبيعات", total_purchases: "إجمالي المشتريات",
  average_invoice: "متوسط الفاتورة", invoices: "الفواتير", lines: "البنود",
  movements: "الحركات", entries: "القيود", contacts: "الجهات", customers: "العملاء",
  suppliers: "الموردون", items: "الأصناف", accounts: "الحسابات", rows: "الصفوف",
  months: "الشهور", bands: "الفئات", buckets: "الفئات", by_type: "حسب النوع",
  asset_rows: "الأصول", liability_rows: "الالتزامات", equity_rows: "حقوق الملكية",
  inflows: "التدفقات الداخلة", outflows: "التدفقات الخارجة", net_change: "صافي التغير",
  discount_total: "إجمالي الخصم", sold_quantity: "الكمية المبيعة",
};

const REPORT_PARAM_LABELS = {
  start: "من تاريخ", end: "إلى تاريخ", upto: "حتى تاريخ",
  as_of: "بتاريخ", days: "عدد الأيام", year: "السنة", limit: "الحد الأقصى",
};

// A pivot column key is a measure plus a dimension value ("net مبيعات"), so an
// unknown key is translated token by token before falling back to the raw text.
const columnLabel = (key) => {
  if (COLUMN_LABELS[key]) return COLUMN_LABELS[key];
  const text = String(key).replace(/_/g, " ");
  const translated = text
    .split(" ")
    .map((token) => COLUMN_LABELS[token] || token)
    .join(" ");
  return translated;
};
const isPercentKey = (key) => /(percent|margin)/.test(key);
const isDateKey = (key) => /(^|_)(date|received_at|closed_at|as_of)($|_)/.test(key) || key === "date";
const isMoneyKey = (key) =>
  /(amount|total|balance|cost|revenue|income|expense|profit|value|price|tax|debit|credit|discount|paid|outstanding|closing|opening|due|net|gross|cash|receivables|payables|shortfall|spend)/.test(key) &&
  !isPercentKey(key) && !/(count|quantity|qty|days)/.test(key);

function formatCell(key, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "نعم" : "لا";
  if (isPercentKey(key)) return `${Number(value).toFixed(2)}%`;
  if (isMoneyKey(key)) return money(value);
  if (isDateKey(key)) return date(value);
  if (typeof value === "number" && !Number.isInteger(value)) return Number(value).toFixed(2);
  return escapeHtml(String(value));
}

/** A row with nested objects collapsed into columns, matching the Excel export. */
function flattenRow(row) {
  const flat = {};
  for (const [key, value] of Object.entries(row)) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const [inner, innerValue] of Object.entries(value)) flat[`${key} ${inner}`] = innerValue;
    } else if (Array.isArray(value)) {
      flat[`${key} count`] = value.length;
    } else {
      flat[key] = value;
    }
  }
  return flat;
}

function reportTable(rows) {
  if (!rows.length) return emptyState("✓", "لا توجد بيانات", "لا شيء لعرضه في هذا القسم");
  const flat = rows.map(flattenRow);
  const columns = [];
  for (const row of flat) for (const key of Object.keys(row)) if (!columns.includes(key)) columns.push(key);
  const numeric = (key) => (isMoneyKey(key) || isPercentKey(key) ? "num" : "");
  return `<div class="card card--flush table-wrap"><table>
    <thead><tr>${columns
      .map((key) => `<th class="${numeric(key)}">${escapeHtml(columnLabel(key))}</th>`)
      .join("")}</tr></thead>
    <tbody>${flat
      .map(
        (row) => `<tr>${columns
          .map((key) => `<td class="${numeric(key)}">${formatCell(key, row[key])}</td>`)
          .join("")}</tr>`
      )
      .join("")}</tbody></table></div>`;
}

/* The custom (pivot) report: the user picks dimensions and measures from a
 * fixed list, so nothing they choose can reach the database as SQL. The lists
 * mirror the server whitelist; an unknown name is rejected there regardless. */
const PIVOT_DIMENSIONS = [
  ["party", "الجهة"], ["item", "الصنف"], ["warehouse", "المخزن"],
  ["month", "الشهر"], ["year", "السنة"], ["kind", "النوع"], ["source", "المصدر"],
];
const PIVOT_MEASURES = [
  ["net", "صافي"], ["cost", "التكلفة"], ["profit", "الربح"],
  ["quantity", "الكمية"], ["tax", "الضريبة"], ["discount", "الخصم"], ["total", "الإجمالي"],
];

/** A chip list: tap to toggle, so it works with a thumb on a phone. */
function pivotChips(target, options, selected) {
  return `<div class="chips" data-chips="${target}">${options
    .map(
      ([key, label]) =>
        `<button type="button" class="chip${selected.includes(key) ? " chip--on" : ""}"
          data-value="${key}" aria-pressed="${selected.includes(key)}">${escapeHtml(label)}</button>`
    )
    .join("")}</div>`;
}

function pivotParams(view) {
  const params = {};
  for (const target of ["rows", "columns", "values"]) {
    const chosen = [...view.querySelectorAll(`[data-chips="${target}"] .chip--on`)].map((c) => c.dataset.value);
    if (chosen.length) params[target] = chosen.join(",");
  }
  for (const name of ["start", "end", "kind"]) {
    const input = view.querySelector(`[data-param="${name}"]`);
    if (input && input.value) params[name] = input.value;
  }
  return params;
}

function pivotPanel(params) {
  const rows = (params.rows || "month").split(",").filter(Boolean);
  const columns = (params.columns || "").split(",").filter(Boolean);
  const values = (params.values || "net").split(",").filter(Boolean);
  return `<div class="card">
    <div class="field"><label class="field__label">الصفوف (اضغط للاختيار)</label>
      ${pivotChips("rows", PIVOT_DIMENSIONS, rows)}</div>
    <div class="field"><label class="field__label">الأعمدة (اختياري)</label>
      ${pivotChips("columns", PIVOT_DIMENSIONS, columns)}</div>
    <div class="field"><label class="field__label">القيم</label>
      ${pivotChips("values", PIVOT_MEASURES, values)}</div>
    <div class="field"><label class="field__label">من تاريخ</label>
      <input class="input" type="date" data-param="start" value="${escapeHtml(params.start || "")}" /></div>
    <div class="field"><label class="field__label">إلى تاريخ</label>
      <input class="input" type="date" data-param="end" value="${escapeHtml(params.end || "")}" /></div>
    <div class="field"><label class="field__label">نوع الفاتورة</label>
      <select class="input" data-param="kind">
        <option value="">الكل</option>
        <option value="sales"${params.kind === "sales" ? " selected" : ""}>مبيعات</option>
        <option value="purchase"${params.kind === "purchase" ? " selected" : ""}>مشتريات</option>
      </select></div>
    <button class="btn btn--block" type="button" data-run>تشغيل التقرير</button>
  </div>`;
}

/** Render any report payload: totals block, then a table per list. */
function renderReportBody(data) {
  // Keys starting with "_" describe the report (its dimensions and measures),
  // not its data, so they are not rendered as tables.
  const entries = Object.entries(data).filter(([key]) => !key.startsWith("_"));
  const scalars = entries.filter(([, v]) => v === null || typeof v !== "object");
  const maps = entries.filter(([, v]) => v && typeof v === "object" && !Array.isArray(v));
  const tables = entries.filter(([, v]) => Array.isArray(v) && v.some((row) => row && typeof row === "object"));
  const lists = entries.filter(([, v]) => Array.isArray(v) && v.every((row) => row === null || typeof row !== "object"));

  let html = "";
  if (scalars.length) {
    html += `<div class="card">${scalars
      .map(
        ([key, value]) => `<div class="totals__row"><span>${escapeHtml(columnLabel(key))}</span>
          <span class="num">${formatCell(key, value)}</span></div>`
      )
      .join("")}</div>`;
  }
  for (const [key, value] of maps) {
    const inner = Object.entries(value).filter(([, v]) => v === null || typeof v !== "object");
    if (!inner.length) continue;
    html += `<div class="section-title">${escapeHtml(columnLabel(key))}</div>
      <div class="card">${inner
        .map(
          ([innerKey, innerValue]) => `<div class="totals__row"><span>${escapeHtml(columnLabel(innerKey))}</span>
            <span class="num">${formatCell(innerKey, innerValue)}</span></div>`
        )
        .join("")}</div>`;
  }
  for (const [key, value] of tables) {
    html += `<div class="section-title">${escapeHtml(columnLabel(key))}</div>${reportTable(value)}`;
  }
  for (const [key, value] of lists) {
    if (!value.length) continue;
    html += `<div class="section-title">${escapeHtml(columnLabel(key))}</div>
      <div class="card card--flush"><ul class="list">${value
        .map((item) => `<li class="list__item"><div class="list__main">${formatCell(key, item)}</div></li>`)
        .join("")}</ul></div>`;
  }
  return html || emptyState("✓", "لا توجد بيانات", "لا شيء لعرضه في هذا التقرير");
}

/** Read the filter inputs into a params object, omitting blanks. */
function reportParams(view, spec) {
  const params = {};
  for (const name of spec.params) {
    const input = view.querySelector(`[data-param="${name}"]`);
    if (input && input.value) params[name] = input.value;
  }
  return params;
}

function reportFilterBar(spec) {
  if (!spec.params.length) return "";
  const fields = spec.params
    .map((name) => {
      const type = ["days", "year", "limit", "warehouse_id"].includes(name) ? "number" : "date";
      const value = name === "limit" ? 'value="50"' : "";
      return `<div class="field"><label class="field__label">${escapeHtml(REPORT_PARAM_LABELS[name] || name)}</label>
        <input class="input" type="${type}" data-param="${escapeHtml(name)}" ${value} /></div>`;
    })
    .join("");
  return `<div class="card">${fields}<button class="btn btn--block" type="button" data-run>تشغيل التقرير</button></div>`;
}

async function downloadReport(key, params, label) {
  const query = new URLSearchParams(params).toString();
  const response = await fetch(`/api/v1/reports/${key}/export.xlsx${query ? `?${query}` : ""}`, {
    headers: {
      Authorization: `Bearer ${store.accessToken}`,
      "X-Company-Id": String(store.companyId),
    },
  });
  if (!response.ok) throw new Error("تعذّر تصدير التقرير");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${label}.xlsx`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  toast("تم تنزيل ملف Excel", "success");
}

let reportCatalog = null;

async function loadReportCatalog() {
  if (!reportCatalog) reportCatalog = await api("/reports/catalog");
  return reportCatalog;
}

async function renderReports() {
  setScreen(spinner(), { appbar: appbar({ title: "التقارير" }), tabbar: tabbar() });
  try {
    const catalog = await loadReportCatalog();
    const view = el(`<div>
      <div class="field"><input class="input" type="search" id="report-search"
        placeholder="ابحث عن تقرير…" autocomplete="off" /></div>
      <div data-categories>${catalog.categories
        .map(
          (category) => `<div data-category>
            <div class="section-title">${escapeHtml(category.label)}</div>
            <div class="card card--flush"><ul class="list">${category.reports
              .map(
                (report) => `<li data-report="${escapeHtml(report.key)}" data-label="${escapeHtml(report.label)}">
                  <a class="list__item" href="#/reports/${report.key}">
                    <span style="font-size:1.4rem">${report.icon}</span>
                    <div class="list__main">
                      <div class="list__title">${escapeHtml(report.label)}</div>
                      ${report.description ? `<div class="list__meta">${escapeHtml(report.description)}</div>` : ""}
                    </div>
                    <span style="color:var(--ink-400)">‹</span></a></li>`
              )
              .join("")}</ul></div></div>`
        )
        .join("")}</div>
      <div class="section-title">الدفتر اليومي</div>
      <div class="card card--flush"><ul class="list"><li><a class="list__item" href="#/journal">
        <span style="font-size:1.4rem">📒</span>
        <div class="list__main"><div class="list__title">القيود المحاسبية</div>
          <div class="list__meta">عرض وإضافة قيود يدوية</div></div>
        <span style="color:var(--ink-400)">‹</span></a></li></ul></div>
    </div>`);

    // A live filter: typing hides reports whose name does not match, and hides
    // a category once none of its reports match.
    view.querySelector("#report-search").addEventListener("input", (event) => {
      const term = event.target.value.trim();
      view.querySelectorAll("[data-report]").forEach((node) => {
        node.hidden = term && !node.dataset.label.includes(term);
      });
      view.querySelectorAll("[data-category]").forEach((node) => {
        node.hidden = !node.querySelector("[data-report]:not([hidden])");
      });
    });

    setScreen(view, { appbar: appbar({ title: "التقارير" }), tabbar: tabbar() });
  } catch (error) {
    setScreen(emptyState("⚠️", "تعذّر تحميل التقارير", error.message), { tabbar: tabbar() });
  }
}

async function renderReport(key, params = {}, back = "#/reports") {
  const catalog = await loadReportCatalog().catch(() => null);
  const spec =
    catalog?.categories.flatMap((c) => c.reports).find((r) => r.key === key) ||
    { key, label: "تقرير", params: [], icon: "📄" };
  setScreen(spinner(), { appbar: appbar({ title: spec.label, back }) });

  const draw = async () => {
    const query = new URLSearchParams(params).toString();
    const data = await api(`/reports/${key}${query ? `?${query}` : ""}`);
    const isPivot = spec.key === "custom";
    const view = el(`<div>
      ${isPivot ? pivotPanel(params) : reportFilterBar(spec)}
      <div class="action-bar">
        <button class="btn btn--ghost" type="button" data-export>⬇️ تصدير Excel</button>
      </div>
      ${renderReportBody(data)}
    </div>`);

    // Chips toggle in place; a pivot needs no re-render just to change a choice.
    view.querySelectorAll(".chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        chip.classList.toggle("chip--on");
        chip.setAttribute("aria-pressed", chip.classList.contains("chip--on"));
      });
    });

    // Keep the entered filters so the export uses the same range on screen.
    for (const [name, value] of Object.entries(params)) {
      const input = view.querySelector(`[data-param="${name}"]`);
      if (input) input.value = value;
    }
    view.querySelector("[data-run]")?.addEventListener("click", () =>
      renderReport(key, isPivot ? pivotParams(view) : reportParams(view, spec), back)
    );
    view.querySelector("[data-export]").addEventListener("click", async (event) => {
      event.target.disabled = true;
      try {
        await downloadReport(key, isPivot ? pivotParams(view) : reportParams(view, spec), spec.label);
      } catch (error) {
        toast(error.message, "error");
      } finally {
        event.target.disabled = false;
      }
    });

    setScreen(view, { appbar: appbar({ title: spec.label, back }) });
  };

  try {
    await draw();
  } catch (error) {
    setScreen(emptyState("⚠️", "تعذّر تحميل التقرير", error.message), { appbar: appbar({ title: spec.label, back }) });
  }
}

// ---------------------------------------------------------------- journal

async function renderJournal() {
  setScreen(spinner(), { appbar: appbar({ title: "قيود اليومية", back: "#/reports" }) });
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
    setScreen(view, { appbar: appbar({ title: "قيود اليومية", back: "#/reports" }) });
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

// ------------------------------------------------- accounts (cash & banks)

/** Cash and bank accounts are assets under 1xxx. A "cash" account is 1010 and
 * its children; a "bank" account is 1020 and its children. Anything else under
 * assets (receivables, inventory) is not cash, so it stays out of this screen. */
const CASH_ROOTS = { cash: "1010", bank: "1020" };

async function renderAccounts() {
  const expanded = new Set((hashParam("open") || "cash,bank").split(",").filter(Boolean));
  setScreen(spinner(), { appbar: appbar({ title: "الحسابات النقدية والبنوك" }), tabbar: tabbar() });
  try {
    const [accounts, report] = await Promise.all([loadAccounts(true), api("/reports/account-balances")]);
    // account-balances groups by type and carries the live balance, so one call
    // gives both the tree and the numbers without a request per account.
    const balances = new Map();
    for (const group of Object.values(report.by_type || {})) {
      for (const a of group) balances.set(a.id, a);
    }
    const byParent = new Map();
    for (const a of accounts) {
      const key = a.parent_id ?? 0;
      if (!byParent.has(key)) byParent.set(key, []);
      byParent.get(key).push(a);
    }
    const balanceOf = (a) => Number(balances.get(a.id)?.balance ?? 0);

    // A root's balance is the sum of its whole subtree, because the leaf holds
    // the money and the group is only a heading.
    const subtreeBalance = (id) => {
      const children = byParent.get(id) || [];
      if (!children.length) return balanceOf(accounts.find((a) => a.id === id) || {});
      return children.reduce((sum, child) => sum + subtreeBalance(child.id), 0);
    };

    const rowsFor = (root, depth) => {
      const children = byParent.get(root.id) || [];
      return children
        .map((child) => {
          const hasChildren = (byParent.get(child.id) || []).length > 0;
          const open = expanded.has(`c${child.id}`);
          const arrow = hasChildren
            ? `<button class="tree-toggle" type="button" data-toggle-account="${child.id}"
                 aria-expanded="${open}" aria-label="إظهار الحسابات الفرعية">${open ? "▾" : "▸"}</button>`
            : '<span class="tree-toggle tree-toggle--empty"></span>';
          const row = `<li><div class="list__item">
            <div class="list__main" style="padding-inline-start:${depth * 14}px">
              <div class="list__title">${arrow}<a href="#/ledger/${child.id}">${escapeHtml(child.name_ar || child.name)}</a></div>
              <div class="list__meta num">${escapeHtml(child.code)}</div></div>
            <div class="list__value num">${money(subtreeBalance(child.id))}</div></div></li>`;
          return open ? row + rowsFor(child, depth + 1) : row;
        })
        .join("");
    };

    const groups = Object.entries(CASH_ROOTS).map(([group, code]) => {
      const root = accounts.find((a) => a.code === code);
      if (!root) return "";
      const open = expanded.has(group);
      const children = byParent.get(root.id) || [];
      return `<div class="section-title">${group === "cash" ? "النقدية" : "البنك"}</div>
        <div class="card card--flush">
          <div class="list__item list__item--head">
            <div class="list__main">
              <div class="list__title">
                <button class="tree-toggle" type="button" data-toggle-account="${group}"
                  aria-expanded="${open}" aria-label="إظهار/إخفاء">${open ? "▾" : "▸"}</button>
                ${escapeHtml(root.name_ar || root.name)}
              </div>
              <div class="list__meta num">${escapeHtml(root.code)}</div></div>
            <div class="list__value num">${money(subtreeBalance(root.id))}</div>
          </div>
          ${open ? `<ul class="list">${children.length ? rowsFor(root, 1) : '<li class="list__empty">لا توجد حسابات فرعية</li>'}</ul>` : ""}
        </div>`;
    }).join("");

    const view = el(`<div>
      ${groups || emptyState("🏦", "لا توجد حسابات نقدية", "أضف حساب نقدية أو بنك للبدء")}
      <div class="action-bar">
        <button class="btn" data-add-cash>＋ حساب جديد</button>
        <a class="btn btn--ghost" href="#/ledger">دليل الحسابات</a>
      </div>
    </div>`);

    view.querySelectorAll("[data-toggle-account]").forEach((btn) => {
      btn.onclick = () => {
        const key = btn.dataset.toggleAccount;
        const token = ["cash", "bank"].includes(key) ? key : `c${key}`;
        if (expanded.has(token)) expanded.delete(token);
        else expanded.add(token);
        location.hash = `#/accounts?open=${[...expanded].join(",")}`;
      };
    });
    view.querySelector("[data-add-cash]").onclick = () => openAccountSheet(accounts, renderAccounts);

    setScreen(view, { appbar: appbar({ title: "الحسابات النقدية والبنوك" }), tabbar: tabbar() });
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
}

/** Add a cash or bank account. It is an asset under 1010 (cash) or 1020 (bank),
 * which is what makes it appear on the cash screen and in the cash reports. */
function openAccountSheet(accounts, onDone) {
  const assets = accounts.filter((a) => a.type === "asset");
  const roots = [
    { value: "1010", label: "نقدية" },
    { value: "1020", label: "بنك" },
  ].filter((r) => assets.some((a) => a.code === r.value));
  const parentOptions = assets
    .filter((a) => /^10\d\d$/.test(a.code))
    .map((a) => `<option value="${a.id}">${escapeHtml(a.code)} — ${escapeHtml(a.name_ar || a.name)}</option>`)
    .join("");

  const form = el(`<form novalidate>
    <div class="field"><label class="field__label">النوع</label>
      <select class="select" id="acc_kind">
        ${roots.map((r) => `<option value="${r.value}">${r.label}</option>`).join("")}
      </select></div>
    <div class="field"><label class="field__label">الاسم</label>
      <input class="input" id="acc_name" required placeholder="مثال: خزينة الفرع، بنك مصر" /></div>
    <div class="field"><label class="field__label">كود الحساب</label>
      <input class="input num" id="acc_code" placeholder="اتركه فارغاً للتوليد التلقائي" inputmode="numeric" /></div>
    <div class="field"><label class="field__label">الحساب الأب</label>
      <select class="select" id="acc_parent">
        <option value="">حسب النوع (تلقائي)</option>${parentOptions}
      </select></div>
    <button class="btn btn--block" type="submit">حفظ الحساب</button>
  </form>`);

  const close = openSheet("حساب نقدية/بنك جديد", form);
  form.onsubmit = async (event) => {
    event.preventDefault();
    const kind = form.querySelector("#acc_kind").value;
    const name = form.querySelector("#acc_name").value.trim();
    if (!name) return toast("أدخل اسم الحساب", "error");
    const chosenParent = form.querySelector("#acc_parent").value;
    const parent = chosenParent ? Number(chosenParent) : assets.find((a) => a.code === kind)?.id ?? null;
    // Default the code to the parent's code + a running suffix, so a new bank
    // account lands next to 1020 as 1021, 1022 … rather than colliding.
    let code = form.querySelector("#acc_code").value.trim();
    if (!code) {
      const siblings = assets.filter((a) => a.parent_id === parent);
      const base = Number(kind) * 1000;
      const used = new Set(siblings.map((a) => Number(a.code)).filter(Number.isFinite));
      let candidate = base + 1;
      while (used.has(candidate)) candidate += 1;
      code = String(candidate);
    }
    try {
      await api("/accounts", {
        method: "POST",
        body: { code, name, name_ar: name, type: "asset", parent_id: parent, is_group: false },
      });
      toast("تم إضافة الحساب", "success");
      close();
      await onDone();
    } catch (error) {
      toast(error.message, "error");
    }
  };
}

// ------------------------------------------------- inventory (items & warehouses)

async function renderInventory() {
  setScreen(spinner(), { appbar: appbar({ title: "الأصناف والمخازن" }), tabbar: tabbar() });
  try {
    const [stock, summary, warehouses] = await Promise.all([
      api("/inventory/stock"),
      api("/inventory/summary"),
      api("/inventory/warehouses"),
    ]);
    const rows = stock
      .map(
        (s) => `<tr>
        <td>${escapeHtml(s.item_name_ar || s.item_name)}</td>
        <td>${escapeHtml(s.warehouse_name)}</td>
        <td>${escapeHtml(s.unit)}</td>
        <td class="num">${Number(s.quantity).toLocaleString("en-US")}</td>
        <td class="num">${money(s.unit_cost)}</td>
        <td class="num">${money(s.value)}</td></tr>`
      )
      .join("");
    setScreen(
      el(`<div>
        <div class="kpi-grid">
          <div class="kpi"><div class="kpi__label">قيمة المخزون</div>
            <div class="kpi__value num">${money(summary.total_value)}</div></div>
          <div class="kpi"><div class="kpi__label">عدد الأصناف</div>
            <div class="kpi__value num">${Number(summary.item_count).toLocaleString("en-US")}</div></div>
          <div class="kpi"><div class="kpi__label">عدد المخازن</div>
            <div class="kpi__value num">${Number(summary.warehouse_count).toLocaleString("en-US")}</div></div>
          <div class="kpi kpi--assets"><div class="kpi__label">طريقة التكلفة</div>
            <div class="kpi__value" style="font-size:1rem">${escapeHtml(summary.costing_method)}</div></div>
        </div>
        <div class="card card--flush">
          ${stock.length ? `<div class="table-wrap"><table>
            <thead><tr><th>الصنف</th><th>المخزن</th><th>الوحدة</th><th class="num">الكمية</th>
              <th class="num">تكلفة الوحدة</th><th class="num">القيمة</th></tr></thead>
            <tbody>${rows}</tbody></table></div>`
            : emptyState("📦", "لا يوجد مخزون", "أضف أصنافاً واستلم مخزوناً للبدء")}
        </div>
        <div class="card"><div class="card__head"><h2 class="card__title">المخازن</h2></div>
          <div class="list__meta">${warehouses.map((w) => escapeHtml(w.name_ar || w.name)).join(" · ") || "لا توجد مخازن"}</div>
        </div>
      </div>`),
      { appbar: appbar({ title: "الأصناف والمخازن" }), tabbar: tabbar() }
    );
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
}

// ------------------------------------------------- ledger (chart of accounts)

async function renderLedger(accountId) {
  if (accountId) return renderReport("account-balances", {}, "#/ledger");
  setScreen(spinner(), { appbar: appbar({ title: "شجرة الحسابات والميزان" }), tabbar: tabbar() });
  try {
    const [accounts, trial] = await Promise.all([loadAccounts(true), api("/reports/trial-balance")]);
    const TYPE_LABELS = { asset: "الأصول", liability: "الالتزامات", equity: "حقوق الملكية", income: "الإيرادات", expense: "المصروفات" };
    // One section per account type, parents bolded and children indented, so the
    // chart reads as a tree rather than a flat list.
    const byType = {};
    for (const a of accounts) (byType[a.type] ||= []).push(a);
    const tree = Object.entries(TYPE_LABELS)
      .filter(([type]) => byType[type]?.length)
      .map(
        ([type, label]) => `<div class="section-title">${label}</div>
        <div class="card card--flush"><ul class="list">${byType[type]
          .map(
            (a) => `<li><a class="list__item" href="#/ledger/${a.id}">
            <div class="list__main" ${a.parent_id ? 'style="padding-inline-start:16px"' : ""}>
              <div class="list__title" ${a.is_group ? 'style="font-weight:800"' : ""}>${escapeHtml(a.name_ar || a.name)}</div>
              <div class="list__meta num">${escapeHtml(a.code)}</div></div>
            <span style="color:var(--ink-400)">‹</span></a></li>`
          )
          .join("")}</ul></div>`
      )
      .join("");
    const rows = (trial.rows || [])
      .map(
        (r) => `<tr><td class="num">${escapeHtml(String(r.code || ""))}</td>
        <td>${escapeHtml(r.name_ar || r.name || "")}</td>
        <td class="num">${money(r.debit)}</td><td class="num">${money(r.credit)}</td>
        <td class="num">${money(r.balance)}</td></tr>`
      )
      .join("");
    setScreen(
      el(`<div>
        ${tree || emptyState("📒", "لا توجد حسابات", "دليل الحسابات فارغ")}
        ${rows ? `<div class="section-title">ميزان المراجعة</div>
          <div class="card card--flush"><div class="table-wrap"><table>
            <thead><tr><th class="num">رقم الحساب</th><th>اسم الحساب</th>
              <th class="num">مدين</th><th class="num">دائن</th><th class="num">الرصيد</th></tr></thead>
            <tbody>${rows}</tbody>
            <tfoot><tr class="total-row"><td colspan="2">الإجمالي</td>
              <td class="num">${money(trial.total_debit)}</td>
              <td class="num">${money(trial.total_credit)}</td>
              <td class="num">${trial.balanced ? "متوازن ✓" : "غير متوازن"}</td></tr></tfoot>
          </table></div></div>` : ""}
      </div>`),
      { appbar: appbar({ title: "شجرة الحسابات والميزان" }), tabbar: tabbar() }
    );
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
}

// ------------------------------------------------- statements (financials)

async function renderStatements() {
  const entries = [
    { key: "income-statement", icon: "📈", label: "قائمة الدخل" },
    { key: "balance-sheet", icon: "⚖️", label: "الميزانية العمومية" },
    { key: "trial-balance", icon: "📋", label: "ميزان المراجعة" },
    { key: "cash-flow", icon: "💧", label: "التدفق النقدي" },
  ];
  setScreen(
    el(`<div>
      <div class="card card--flush"><ul class="list">${entries
        .map(
          (e) => `<li><a class="list__item" href="#/statements/${e.key}">
          <span style="font-size:1.3rem">${e.icon}</span>
          <div class="list__main"><div class="list__title">${e.label}</div></div>
          <span style="color:var(--ink-400)">‹</span></a></li>`
        )
        .join("")}</ul></div>
    </div>`),
    { appbar: appbar({ title: "القوائم المالية" }), tabbar: tabbar() }
  );
}

// ---------------------------------------------------------------- settings

/** Clear the session locally and on the server, then return to the login screen. */
async function logout() {
  try {
    await api("/auth/logout", { method: "POST", body: { refresh_token: store.session.refresh_token } });
  } catch {
    /* logging out locally is enough */
  }
  store.reset();
  renderAuth();
}

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
        <ul class="list">
          <li><a class="list__item" href="#/settings" data-settings-link>
          <span style="font-size:1.4rem">⚙️</span>
          <div class="list__main"><div class="list__title">الإعدادات والضرائب</div>
            <div class="list__meta">بيانات الشركة وأكواد الضريبة</div></div>
          <span style="color:var(--ink-400)">‹</span></a></li>
          <li><a class="list__item" href="#/team" data-team-link>
          <span style="font-size:1.4rem">👥</span>
          <div class="list__main"><div class="list__title">الفريق والصلاحيات</div>
            <div class="list__meta">إضافة مستخدمين وتحديد الأدوار</div></div>
          <span style="color:var(--ink-400)">‹</span></a></li>
        </ul>
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
    close();
    await logout();
  };
}

// ---------------------------------------------------------------- team

const ROLE_LABELS = {
  owner: "مالك",
  admin: "مدير",
  accountant: "محاسب",
  sales: "مبيعات",
  storekeeper: "أمين مخزن",
  viewer: "مشاهدة فقط",
  customer: "عميل (بوابة)",
};

async function renderTeam() {
  setScreen(spinner(), { appbar: appbar({ title: "الموظفون والصلاحيات", back: "#/dashboard" }) });
  try {
    const members = await api("/team");
    const rows = members
      .map(
        (m) => `<li class="list__item">
        <div class="list__main">
          <div class="list__title">${escapeHtml(m.full_name || m.email)}</div>
          <div class="list__meta">${escapeHtml(m.email)} · ${escapeHtml(ROLE_LABELS[m.role] || m.role)}</div>
        </div>
        <button class="btn btn--ghost btn--sm" data-edit="${m.id}" data-role="${escapeHtml(m.role)}"
          data-active="${m.is_active}">تعديل</button></li>`
      )
      .join("");

    const view = el(`<div>
      <div class="card card--flush">
        <ul class="list">${rows}</ul>
      </div>
      <button class="btn btn--block" data-add>＋ إضافة عضو</button>
    </div>`);

    view.querySelector("[data-add]").onclick = () => {
      let close = () => {};
      close = openSheet("إضافة عضو", buildMemberForm({ onDone: () => { close(); renderTeam(); } }));
    };
    view.querySelectorAll("[data-edit]").forEach((button) => {
      button.onclick = () => {
        let close = () => {};
        close = openSheet(
          "تعديل العضو",
          buildMemberForm({
            memberId: Number(button.dataset.edit),
            role: button.dataset.role,
            isActive: button.dataset.active === "true",
            onDone: () => {
              close();
              renderTeam();
            },
          })
        );
      };
    });

    setScreen(view, { appbar: appbar({ title: "الموظفون والصلاحيات", back: "#/dashboard" }) });
  } catch (error) {
    setScreen(emptyState("⚠️", "تعذّر تحميل الفريق", error.message), { tabbar: tabbar() });
  }
}

function buildMemberForm({ memberId, role, isActive = true, onDone }) {
  const editing = Boolean(memberId);
  const options = Object.entries(ROLE_LABELS)
    .map(
      ([value, label]) =>
        `<option value="${value}" ${value === role ? "selected" : ""}>${escapeHtml(label)}</option>`
    )
    .join("");

  const form = el(`<form novalidate>
    ${editing
      ? ""
      : `<div class="field"><label class="field__label" for="m_email">البريد الإلكتروني</label>
          <input class="input" id="m_email" type="email" required /></div>
        <div class="field"><label class="field__label" for="m_name">الاسم</label>
          <input class="input" id="m_name" /></div>
        <div class="field"><label class="field__label" for="m_password">كلمة مرور مبدئية</label>
          <input class="input" id="m_password" type="password" required />
          <div class="field__hint">يستخدمها العضو لأول تسجيل دخول ثم يغيّرها</div></div>`}
    <div class="field"><label class="field__label" for="m_role">الدور</label>
      <select class="select" id="m_role" required>${options}</select></div>
    ${editing
      ? `<label class="field__label" style="display:flex;gap:8px;align-items:center">
          <input type="checkbox" id="m_active" ${isActive ? "checked" : ""} /> العضوية نشطة</label>`
      : ""}
    <button class="btn btn--block" type="submit">${editing ? "حفظ التعديلات" : "إضافة العضو"}</button>
  </form>`);

  form.onsubmit = async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      if (editing) {
        await api(`/team/${memberId}`, { method: "PATCH", body: { role: form.querySelector("#m_role").value } });
        if (!form.querySelector("#m_active").checked) {
          await api(`/team/${memberId}`, { method: "DELETE" });
        }
        toast("تم تحديث العضو", "success");
      } else {
        await api("/team", {
          method: "POST",
          body: {
            email: form.querySelector("#m_email").value.trim(),
            full_name: form.querySelector("#m_name").value.trim(),
            role: form.querySelector("#m_role").value,
            password: form.querySelector("#m_password").value,
          },
        });
        toast("تمت إضافة العضو", "success");
      }
      await onDone();
    } catch (error) {
      toast(error.message, "error");
      button.disabled = false;
    }
  };
  return form;
}

// ---------------------------------------------------------------- assistant

/** Quick questions answered from reports the app already computes. There is no
 * external model here: each button runs a report and formats its figures, so an
 * answer can never disagree with the report it came from. */
const ASSISTANT_QUICK = [
  { key: "sales", label: "إجمالي المبيعات" },
  { key: "top_item", label: "أكثر صنف مبيعًا" },
  { key: "balance", label: "رصيد عميل" },
  { key: "work_order", label: "تكلفة أمر إنتاج" },
];

async function renderAssistant(answer = null) {
  const view = el(`<div>
    <div class="card">
      <div class="card__head"><h2 class="card__title">أسئلة سريعة</h2></div>
      <div class="assistant__quick">
        ${ASSISTANT_QUICK.map((q) => `<button class="btn btn--ghost btn--sm" data-ask="${q.key}">${q.label}</button>`).join("")}
      </div>
      <div class="field" style="margin-top:12px">
        <label class="field__label" for="assistant-q">اكتب سؤالك</label>
        <textarea class="input" id="assistant-q" rows="3" placeholder="مثال: ما رصيد العميل؟"></textarea>
      </div>
      <button class="btn btn--block" data-ask="free">اسأل</button>
    </div>
    <div id="assistant-answer">${answer || ""}</div>
  </div>`);

  const answerBox = view.querySelector("#assistant-answer");
  const ask = async (key) => {
    answerBox.innerHTML = spinner();
    try {
      answerBox.innerHTML = await answerQuestion(key, view.querySelector("#assistant-q").value);
    } catch (error) {
      answerBox.innerHTML = `<div class="card"><div class="list__meta">تعذّر الحصول على الإجابة: ${escapeHtml(error.message)}</div></div>`;
    }
  };
  view.querySelectorAll("[data-ask]").forEach((btn) => {
    btn.onclick = () => ask(btn.dataset.ask);
  });

  setScreen(view, { appbar: appbar({ title: "مساعد بيانات الشركة" }), tabbar: tabbar() });
}

function answerCard(title, body) {
  return `<div class="card"><div class="card__head"><h2 class="card__title">${escapeHtml(title)}</h2></div>${body}</div>`;
}

function answerRow(label, value) {
  return `<div class="assistant__row"><span>${escapeHtml(label)}</span><strong class="num">${value}</strong></div>`;
}

/** Answer one question by running the report that owns the figure. The free
 * question is routed by keywords, because a real language model is out of scope
 * and a wrong guess is worse than a clear "not understood". */
async function answerQuestion(key, text = "") {
  const period = `من ${date("2026-01-01")} إلى ${date(today())}`;
  if (key === "free") {
    const q = text.trim();
    if (!q) return answerCard("اسأل", '<div class="list__meta">اكتب سؤالاً أولاً.</div>');
    if (/مبيعات|بيع/.test(q)) key = "sales";
    else if (/صنف|منتج/.test(q)) key = "top_item";
    else if (/رصيد|عميل|مديون/.test(q)) key = "balance";
    else if (/إنتاج|أمر/.test(q)) key = "work_order";
    else return answerCard("لم أفهم السؤال", '<div class="list__meta">جرّب: إجمالي المبيعات، أكثر صنف مبيعًا، رصيد عميل، تكلفة أمر إنتاج.</div>');
  }

  if (key === "sales") {
    const data = await api("/reports/sales-summary");
    return answerCard(
      "إجمالي المبيعات",
      answerRow("صافي المبيعات", money(data.net)) +
        answerRow("عدد الفواتير", Number(data.invoice_count).toLocaleString("en-US")) +
        answerRow("متوسط الفاتورة", money(data.average_invoice)) +
        `<div class="list__meta" style="margin-top:8px">${period}</div>`
    );
  }

  if (key === "top_item") {
    const data = await api("/reports/item-profitability");
    const top = (data.items || [])[0];
    if (!top) return answerCard("أكثر صنف مبيعًا", '<div class="list__meta">لا توجد مبيعات مسجّلة بعد.</div>');
    return answerCard(
      "أكثر صنف مبيعًا",
      answerRow("الصنف", escapeHtml(top.item_name_ar || top.item_name)) +
        answerRow("الكمية", Number(top.quantity).toLocaleString("en-US")) +
        answerRow("الإيراد", money(top.revenue)) +
        answerRow("الربح", money(top.profit))
    );
  }

  if (key === "balance") {
    const data = await api("/reports/customer-balances");
    const rows = (data.contacts || []).slice(0, 5);
    if (!rows.length) return answerCard("أرصدة العملاء", '<div class="list__meta">لا توجد أرصدة مستحقة.</div>');
    return answerCard(
      "أرصدة العملاء",
      rows.map((c) => answerRow(c.name, money(c.balance))).join("") +
        answerRow("الإجمالي", money(data.total))
    );
  }

  if (key === "work_order") {
    const orders = await api("/production/work-orders");
    if (!orders.length) return answerCard("تكلفة أوامر الإنتاج", '<div class="list__meta">لا توجد أوامر إنتاج.</div>');
    const STATUS = { planned: "مخطّط", in_progress: "قيد التنفيذ", completed: "مكتمل", cancelled: "ملغي" };
    // unit_cost is only finalised when the finished goods are received, so a
    // running order reports zero there while its consumed materials are already
    // booked in material_cost. Use whichever is real rather than showing zero.
    const orderCost = (o) => {
      const perUnit = Number(o.unit_cost) * Number(o.produced_quantity);
      return perUnit > 0 ? perUnit : Number(o.material_cost);
    };
    return answerCard(
      "تكلفة أوامر الإنتاج",
      orders
        .slice(0, 5)
        .map((o) => answerRow(`${o.number} — ${STATUS[o.status] || o.status}`, money(orderCost(o))))
        .join("")
    );
  }

  return answerCard("اسأل", '<div class="list__meta">اختر سؤالاً سريعاً أو اكتب سؤالك.</div>');
}

// ---------------------------------------------------------------- timeline (audit trail)

/* Human labels for the action codes the services write. Unknown codes fall
   back to the raw code, so a new action never renders as blank. */
const ACTION_LABELS = {
  "invoice.create": "إنشاء فاتورة",
  "invoice.issue": "إصدار فاتورة",
  "invoice.update": "تعديل فاتورة",
  "invoice.void": "إلغاء فاتورة",
  "invoice.return": "إنشاء مرتجع",
  "payment.create": "تسجيل دفعة",
  "payment.reverse": "عكس دفعة",
  "journal.post": "ترحيل قيد",
  "journal.reverse": "عكس قيد",
  "item.create": "إضافة صنف",
  "item.variant.add": "إضافة مقاس/لون",
  "warehouse.create": "إضافة مخزن",
  "work_order.create": "إنشاء أمر إنتاج",
  "work_order.start": "بدء أمر إنتاج",
  "work_order.complete": "إتمام أمر إنتاج",
  "work_order.cancel": "إلغاء أمر إنتاج",
  "settings.update": "تعديل الإعدادات",
  "tax_code.create": "إضافة كود ضريبة",
  "member.add": "إضافة عضو",
  "member.role": "تغيير دور عضو",
  "member.deactivate": "تعطيل عضو",
};

/* Actions that create money/documents are "positive"; corrections are shown in
   a muted tone so a reversal reads differently from the entry it fixes. */
const NEGATIVE_ACTIONS = new Set([
  "invoice.void", "payment.reverse", "journal.reverse", "work_order.cancel", "member.deactivate",
]);

const actionLabel = (action) => ACTION_LABELS[action] || action;

async function renderTimeline() {
  const search = hashParam("timeline_search");
  const action = hashParam("timeline_action");
  const from = hashParam("timeline_from");
  const to = hashParam("timeline_to");
  setScreen(spinner(), { appbar: appbar({ title: "السجل الزمني" }), tabbar: tabbar() });
  try {
    const query = new URLSearchParams({ limit: "200" });
    if (search) query.set("search", search);
    if (action) query.set("action", action);
    if (from) query.set("date_from", from);
    if (to) query.set("date_to", to);
    const [page, actions] = await Promise.all([api(`/audit-logs?${query}`), api("/audit-logs/actions")]);

    const actionOptions = actions
      .map((code) => `<option value="${escapeHtml(code)}" ${code === action ? "selected" : ""}>${escapeHtml(actionLabel(code))}</option>`)
      .join("");

    const rows = page.items
      .map((entry) => {
        const negative = NEGATIVE_ACTIONS.has(entry.action);
        return `<tr>
          <td class="nowrap num">${escapeHtml(dateTime(entry.created_at))}</td>
          <td>${escapeHtml(entry.user_name)}</td>
          <td><span class="badge badge--${negative ? "void" : "issued"}">${escapeHtml(actionLabel(entry.action))}</span></td>
          <td>${escapeHtml(entry.summary || entry.entity_type)}</td></tr>`;
      })
      .join("");

    const view = el(`<div>
      <div class="card filter-card">
        <div class="filter-card__grid">
          <label class="field"><span class="field__label">بحث (يحتوي على)</span>
            <input class="input" id="tl_search" value="${escapeHtml(search)}" placeholder="المستخدم أو البيان أو المرجع" /></label>
          <label class="field"><span class="field__label">الإجراء</span>
            <select class="input" id="tl_action"><option value="">كل الإجراءات</option>${actionOptions}</select></label>
          <label class="field"><span class="field__label">من تاريخ</span>
            <input class="input" type="date" id="tl_from" value="${escapeHtml(from)}" /></label>
          <label class="field"><span class="field__label">إلى تاريخ</span>
            <input class="input" type="date" id="tl_to" value="${escapeHtml(to)}" /></label>
        </div>
        <div class="filter-card__actions">
          <button class="btn btn--ghost" type="button" id="tl_clear">× مسح</button>
          <button class="btn" type="button" id="tl_apply">تصفية</button>
        </div>
      </div>
      <div class="card card--flush">
        <div class="card__head"><h2 class="card__title">آخر العمليات</h2>
          <span class="list__meta num">${Number(page.total).toLocaleString("en-US")} سجل</span></div>
        ${page.items.length
          ? `<div class="table-wrap"><table>
              <thead><tr><th>التاريخ والوقت</th><th>المستخدم</th><th>الإجراء</th><th>البيان</th></tr></thead>
              <tbody>${rows}</tbody></table></div>`
          : emptyState("◷", "لا توجد حركات", "لم تُسجّل أي عملية بهذه الفلاتر")}
      </div>
    </div>`);

    const go = () => {
      const params = new URLSearchParams();
      const s = view.querySelector("#tl_search").value.trim();
      const a = view.querySelector("#tl_action").value;
      const f = view.querySelector("#tl_from").value;
      const t = view.querySelector("#tl_to").value;
      if (s) params.set("timeline_search", s);
      if (a) params.set("timeline_action", a);
      if (f) params.set("timeline_from", f);
      if (t) params.set("timeline_to", t);
      location.hash = `#/timeline${params.toString() ? `?${params}` : ""}`;
    };
    view.querySelector("#tl_apply").addEventListener("click", go);
    view.querySelector("#tl_search").addEventListener("keydown", (e) => e.key === "Enter" && go());
    view.querySelector("#tl_clear").addEventListener("click", () => {
      location.hash = "#/timeline";
    });

    setScreen(view, { appbar: appbar({ title: "السجل الزمني" }), tabbar: tabbar() });
  } catch (error) {
    setScreen(emptyState("⚠️", "خطأ", error.message), { tabbar: tabbar() });
  }
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
  const parts = path.split("/");
  const [, segment, param, sub] = parts;
  const params = new URLSearchParams(query || "");
  app.dataset.route = hash;

  const publicRoutes = ["login"];
  if (!store.session && !publicRoutes.includes(segment)) {
    renderAuth();
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
      if (param === "new") return renderInvoiceForm();
      if (param && sub === "edit") return renderInvoiceForm({ invoiceId: Number(param) });
      return param ? renderInvoiceDetail(param) : renderInvoices();
    case "payments":
      return renderPayments({ autoNew: params.get("new") });
    case "production":
      return param === "new" ? renderWorkOrderForm() : param ? renderWorkOrderDetail(param) : renderProduction();
    case "contacts":
      return param ? renderContactDetail(param) : renderContacts();
    case "reports":
      return param ? renderReport(param) : renderReports();
    case "statements":
      return param ? renderReport(param, {}, "#/statements") : renderStatements();
    case "ledger":
      return renderLedger(param ? Number(param) : null);
    case "accounts":
      return renderAccounts();
    case "inventory":
      return renderInventory();
    case "journal":
      return param === "new" ? renderJournalForm() : renderJournal();
    case "settings":
      return renderSettings();
    case "team":
      return renderTeam();
    case "timeline":
      return renderTimeline();
    case "assistant":
      return renderAssistant();
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
  if (event.target.closest("[data-toggle-sidebar]")) toggleSidebar();
  if (event.target.closest("[data-close-sidebar]")) toggleSidebar(false);
  if (event.target.closest("[data-logout]")) logout();
  if (event.target.closest("[data-open-theme]")) openThemePicker();
  if (event.target.closest("[data-subtab]")) {
    const [group, key] = event.target.closest("[data-subtab]").dataset.subtab.split(":");
    const [path] = location.hash.split("?");
    location.hash = `${path}?${group}=${key}`;
  }
  if (event.target.closest("[data-toggle-dark]")) {
    const dark = document.documentElement.dataset.theme !== "dark";
    applyDarkMode(dark);
    event.target.closest("[data-toggle-dark]").textContent = dark ? "☀" : "☾";
  }
  // A sidebar link navigates by hash, so the drawer must close by hand on phones.
  if (event.target.closest(".sidebar__link")) toggleSidebar(false);
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
    return renderAuth();
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
loadTheme();
if (store.session) {
  bootstrap().catch(() => {
    store.reset();
    renderAuth();
  });
} else {
  renderAuth();
}
