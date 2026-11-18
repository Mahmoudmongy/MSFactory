/* OpenBooks — single-page mobile accounting UI (no build step). */
"use strict";

const state = {
  token: localStorage.getItem("token") || "",
  user: null,
  route: "dashboard",
  accounts: [],
  contacts: [],
  settings: {},
};

/* ---------------- helpers ---------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const el = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => Number(v || 0);
const fmt = (v) => num(v).toLocaleString("en-EG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const today = () => new Date().toISOString().slice(0, 10);

function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 2600);
}

async function api(path, options = {}) {
  const res = await fetch("/api" + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(state.token ? { Authorization: "Bearer " + state.token } : {}),
      ...(options.headers || {}),
    },
  });
  if (res.status === 401) {
    logout();
    throw new Error("انتهت الجلسة، سجّل الدخول مرة أخرى");
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "حدث خطأ غير متوقع");
  return data;
}

function sheet(title, contentNode) {
  const root = $("#sheet-root");
  const backdrop = el(`<div class="sheet-backdrop"></div>`);
  const box = el(`
    <div class="sheet">
      <div class="sheet-head"><h3>${esc(title)}</h3><button class="close">✕</button></div>
    </div>`);
  box.append(contentNode);
  backdrop.append(box);
  backdrop.addEventListener("click", (e) => { if (e.target === backdrop) close(); });
  box.querySelector(".close").addEventListener("click", close);
  root.append(backdrop);
  function close() { backdrop.remove(); }
  return close;
}

/* ---------------- auth ---------------- */
async function login(username, password) {
  const data = await api("/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  state.token = data.token;
  state.user = data.user;
  localStorage.setItem("token", data.token);
  start();
}

function logout() {
  state.token = "";
  state.user = null;
  localStorage.removeItem("token");
  $("#app").classList.add("hidden");
  $("#login").classList.remove("hidden");
}

async function boot() {
  if (!state.token) return;
  try {
    state.user = await api("/auth/me");
    start();
  } catch { /* token invalid */ }
}

/* ---------------- shell ---------------- */
async function start() {
  $("#login").classList.add("hidden");
  $("#app").classList.remove("hidden");
  await refreshLookups();
  render();
}

async function refreshLookups() {
  [state.accounts, state.contacts, state.settings] = await Promise.all([
    api("/accounts"),
    api("/contacts"),
    api("/settings"),
  ]);
}

const ROUTES = {
  dashboard: { title: "لوحة التحكم", render: renderDashboard },
  invoices: { title: "الفواتير", render: renderInvoices },
  payments: { title: "المدفوعات", render: renderPayments },
  contacts: { title: "العملاء والموردون", render: renderContacts },
  reports: { title: "التقارير", render: renderReports },
  more: { title: "المزيد", render: renderMore },
};

async function render() {
  const route = ROUTES[state.route];
  $("#page-title").textContent = route.title;
  document.querySelectorAll("nav.tabbar button").forEach((b) =>
    b.classList.toggle("active", b.dataset.route === state.route)
  );
  const view = $("#view");
  view.innerHTML = `<div class="empty">جاري التحميل…</div>`;
  try {
    await route.render(view);
  } catch (e) {
    view.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function go(route) {
  state.route = route;
  render();
  window.scrollTo({ top: 0 });
}

/* ---------------- dashboard ---------------- */
async function renderDashboard(view) {
  const d = await api("/reports/dashboard");
  view.innerHTML = `
    <div class="grid">
      <div class="stat"><div class="label">النقدية والبنك</div><div class="value">${fmt(d.cash)}</div></div>
      <div class="stat"><div class="label">الإيرادات</div><div class="value pos">${fmt(d.income)}</div></div>
      <div class="stat"><div class="label">المصروفات</div><div class="value neg">${fmt(d.expense)}</div></div>
      <div class="stat"><div class="label">صافي الربح</div><div class="value ${num(d.net_profit) < 0 ? "neg" : "pos"}">${fmt(d.net_profit)}</div></div>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>المركز المالي</h3>
      <div class="total-row"><span>إجمالي الأصول</span><b>${fmt(d.assets)}</b></div>
      <div class="total-row"><span>إجمالي الالتزامات</span><b>${fmt(d.liabilities)}</b></div>
    </div>
    <div class="card">
      <h3>اختصارات</h3>
      <button class="btn" id="quick-invoice">🧾 فاتورة بيع جديدة</button>
      <button class="btn ghost" id="quick-payment">💵 تسجيل دفعة</button>
      <button class="btn ghost" id="quick-report">📊 قائمة الدخل</button>
    </div>`;
  $("#quick-invoice").onclick = () => invoiceForm("sales");
  $("#quick-payment").onclick = () => paymentForm("receive");
  $("#quick-report").onclick = () => go("reports");
}

/* ---------------- invoices ---------------- */
async function renderInvoices(view) {
  const invoices = await api("/invoices");
  view.innerHTML = `
    <div class="row" style="margin-bottom:12px">
      <button class="btn sm" style="flex:1" id="new-sale">+ فاتورة بيع</button>
      <button class="btn sm ghost" style="flex:1" id="new-bill">+ فاتورة شراء</button>
    </div>
    <div class="list" id="inv-list"></div>`;
  $("#new-sale").onclick = () => invoiceForm("sales");
  $("#new-bill").onclick = () => invoiceForm("purchase");

  const list = $("#inv-list");
  if (!invoices.length) {
    list.innerHTML = `<div class="empty">لا توجد فواتير بعد</div>`;
    return;
  }
  invoices.forEach((inv) => {
    const label = inv.kind === "sales" ? "بيع" : "شراء";
    const badge = inv.status === "paid" ? '<span class="pill ok">مدفوعة</span>'
      : num(inv.balance) < num(inv.total) ? '<span class="pill warn">مدفوعة جزئياً</span>'
      : '<span class="pill">مستحقة</span>';
    const item = el(`
      <div class="item">
        <div class="main">
          <div class="title">${esc(inv.contact_name)} ${badge}</div>
          <div class="sub">${label} · ${esc(inv.number)} · ${esc(inv.issue_date)}</div>
        </div>
        <div class="amount">${fmt(inv.total)}<div class="sub" style="font-weight:400">متأخر: ${fmt(inv.balance)}</div></div>
      </div>`);
    item.onclick = () => showInvoice(inv.id);
    list.append(item);
  });
}

async function invoiceForm(kind) {
  const customers = state.contacts.filter((c) => kind === "sales" ? c.kind !== "supplier" : c.kind !== "customer");
  const revenueAccounts = state.accounts.filter((a) => a.type === (kind === "sales" ? "income" : "expense"));
  const defaultAccount = revenueAccounts.find((a) => a.code === (kind === "sales" ? "4100" : "5100"));

  const form = el(`
    <form>
      <label>الطرف</label>
      <select id="f-contact" required>
        <option value="">— اختر —</option>
        ${customers.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}
      </select>
      <label>تاريخ الفاتورة</label>
      <input type="date" id="f-date" value="${today()}" required />
      <label>تاريخ الاستحقاق</label>
      <input type="date" id="f-due" />
      <label>البنود</label>
      <div id="f-lines"></div>
      <button type="button" class="btn ghost sm" id="add-line" style="width:100%">+ إضافة بند</button>
      <div class="total-row"><span>الإجمالي قبل الضريبة</span><b id="t-sub">0.00</b></div>
      <div class="total-row"><span>الضريبة</span><b id="t-tax">0.00</b></div>
      <div class="total-row grand"><span>الإجمالي</span><b id="t-total">0.00</b></div>
      <label>ملاحظات</label>
      <textarea id="f-notes"></textarea>
      <button class="btn" type="submit">حفظ الفاتورة</button>
    </form>`);

  const linesBox = $("#f-lines", form);
  const vatRate = num(state.settings.vat_rate);

  function addLine() {
    const row = el(`
      <div class="line-row">
        <input placeholder="الوصف" class="l-desc" />
        <input type="number" step="0.01" value="1" class="l-qty" title="الكمية" />
        <input type="number" step="0.01" value="0" class="l-price" title="السعر" />
        <input type="number" step="0.01" value="${vatRate}" class="l-tax" title="نسبة الضريبة" />
        <button type="button" class="del">✕</button>
      </div>`);
    row.querySelector(".del").onclick = () => { wrap.remove(); recalc(); };
    row.querySelectorAll("input").forEach((i) => i.addEventListener("input", recalc));
    const sel = el(`<select class="l-acc" style="margin-bottom:6px">
      ${revenueAccounts.map((a) => `<option value="${a.id}" ${defaultAccount && a.id === defaultAccount.id ? "selected" : ""}>${esc(a.code + " " + a.name)}</option>`).join("")}
    </select>`);
    const wrap = el(`<div class="inv-line" style="border-bottom:1px dashed var(--line); padding-bottom:8px; margin-bottom:8px"></div>`);
    wrap.append(sel, row);
    linesBox.append(wrap);
    recalc();
  }

  function recalc() {
    let sub = 0, tax = 0;
    linesBox.querySelectorAll(".inv-line").forEach((wrap) => {
      const qty = num($(".l-qty", wrap).value);
      const price = num($(".l-price", wrap).value);
      const rate = num($(".l-tax", wrap).value);
      const amount = qty * price;
      sub += amount;
      tax += (amount * rate) / 100;
    });
    $("#t-sub", form).textContent = fmt(sub);
    $("#t-tax", form).textContent = fmt(tax);
    $("#t-total", form).textContent = fmt(sub + tax);
  }

  $("#add-line", form).onclick = addLine;
  addLine();

  form.onsubmit = async (e) => {
    e.preventDefault();
    const lines = [];
    linesBox.querySelectorAll(".inv-line").forEach((wrap) => {
      const desc = $(".l-desc", wrap).value.trim();
      const qty = num($(".l-qty", wrap).value);
      const price = num($(".l-price", wrap).value);
      if (!desc && !price) return;
      lines.push({
        description: desc,
        quantity: qty,
        unit_price: price,
        tax_rate: num($(".l-tax", wrap).value),
        account_id: Number($(".l-acc", wrap).value),
      });
    });
    if (!lines.length) return toast("أضف بنداً واحداً على الأقل", true);
    const payload = {
      kind,
      contact_id: Number($("#f-contact", form).value),
      issue_date: $("#f-date", form).value,
      due_date: $("#f-due", form).value || null,
      notes: $("#f-notes", form).value,
      lines,
    };
    if (!payload.contact_id) return toast("اختر الطرف", true);
    try {
      await api("/invoices", { method: "POST", body: JSON.stringify(payload) });
      closeSheet();
      toast("تم حفظ الفاتورة");
      render();
    } catch (err) { toast(err.message, true); }
  };

  const closeSheet = sheet(kind === "sales" ? "فاتورة بيع جديدة" : "فاتورة شراء جديدة", form);
}

async function showInvoice(id) {
  const inv = await api("/invoices/" + id);
  const body = el(`
    <div>
      <div class="total-row"><span>الطرف</span><b>${esc(inv.contact_name)}</b></div>
      <div class="total-row"><span>الرقم</span><b>${esc(inv.number)}</b></div>
      <div class="total-row"><span>التاريخ</span><b>${esc(inv.issue_date)}</b></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>الوصف</th><th class="num">كمية</th><th class="num">سعر</th><th class="num">الإجمالي</th></tr></thead>
          <tbody>
            ${inv.lines.map((l) => `<tr><td>${esc(l.description)}</td><td class="num">${fmt(l.quantity)}</td><td class="num">${fmt(l.unit_price)}</td><td class="num">${fmt(l.amount)}</td></tr>`).join("")}
          </tbody>
        </table>
      </div>
      <div class="total-row"><span>الإجمالي قبل الضريبة</span><b>${fmt(inv.subtotal)}</b></div>
      <div class="total-row"><span>الضريبة</span><b>${fmt(inv.tax_total)}</b></div>
      <div class="total-row grand"><span>الإجمالي</span><b>${fmt(inv.total)}</b></div>
      <div class="total-row"><span>المدفوع</span><b>${fmt(inv.amount_paid)}</b></div>
      <div class="total-row"><span>المتبقي</span><b>${fmt(inv.balance)}</b></div>
      <button class="btn danger" id="del-inv">حذف الفاتورة</button>
    </div>`);
  const closeSheet = sheet("تفاصيل الفاتورة", body);
  $("#del-inv", body).onclick = async () => {
    if (!confirm("تأكيد حذف الفاتورة؟")) return;
    try {
      await api("/invoices/" + id, { method: "DELETE" });
      closeSheet();
      toast("تم الحذف");
      render();
    } catch (e) { toast(e.message, true); }
  };
}

/* ---------------- payments ---------------- */
async function renderPayments(view) {
  const payments = await api("/payments");
  view.innerHTML = `
    <div class="row" style="margin-bottom:12px">
      <button class="btn sm" style="flex:1" id="new-rcv">+ تحصيل من عميل</button>
      <button class="btn sm ghost" style="flex:1" id="new-pay">+ دفع لمورد</button>
    </div>
    <div class="list" id="pay-list"></div>`;
  $("#new-rcv").onclick = () => paymentForm("receive");
  $("#new-pay").onclick = () => paymentForm("spend");

  const list = $("#pay-list");
  if (!payments.length) { list.innerHTML = `<div class="empty">لا توجد مدفوعات</div>`; return; }
  payments.forEach((p) => {
    const item = el(`
      <div class="item">
        <div class="main">
          <div class="title">${esc(p.contact_name)}</div>
          <div class="sub">${p.kind === "receive" ? "تحصيل" : "دفع"} · ${esc(p.number)} · ${esc(p.date)}</div>
        </div>
        <div class="amount ${p.kind === "receive" ? "pos" : "neg"}">${fmt(p.amount)}</div>
      </div>`);
    item.onclick = async () => {
      if (!confirm("حذف هذه الدفعة؟")) return;
      try { await api("/payments/" + p.id, { method: "DELETE" }); toast("تم الحذف"); render(); }
      catch (e) { toast(e.message, true); }
    };
    list.append(item);
  });
}

function paymentForm(kind) {
  const parties = state.contacts.filter((c) => kind === "receive" ? c.kind !== "supplier" : c.kind !== "customer");
  const banks = state.accounts.filter((a) => ["1200", "1210"].includes(a.code));
  const form = el(`
    <form>
      <label>الطرف</label>
      <select id="p-contact" required><option value="">— اختر —</option>
        ${parties.map((c) => `<option value="${c.id}">${esc(c.name)} (رصيد ${fmt(c.balance)})</option>`).join("")}
      </select>
      <label>التاريخ</label>
      <input type="date" id="p-date" value="${today()}" required />
      <label>المبلغ</label>
      <input type="number" step="0.01" id="p-amount" required />
      <label>حساب النقدية / البنك</label>
      <select id="p-bank">
        ${banks.map((a) => `<option value="${a.id}">${esc(a.code + " " + a.name)}</option>`).join("")}
      </select>
      <label>المرجع</label>
      <input id="p-ref" />
      <button class="btn" type="submit">حفظ الدفعة</button>
    </form>`);
  const closeSheet = sheet(kind === "receive" ? "تحصيل من عميل" : "دفع لمورد", form);
  form.onsubmit = async (e) => {
    e.preventDefault();
    const payload = {
      kind,
      contact_id: Number($("#p-contact", form).value),
      date: $("#p-date", form).value,
      amount: num($("#p-amount", form).value),
      bank_account_id: Number($("#p-bank", form).value),
      reference: $("#p-ref", form).value,
    };
    if (!payload.contact_id) return toast("اختر الطرف", true);
    if (payload.amount <= 0) return toast("أدخل مبلغاً صحيحاً", true);
    try {
      await api("/payments", { method: "POST", body: JSON.stringify(payload) });
      closeSheet();
      toast("تم تسجيل الدفعة");
      render();
    } catch (err) { toast(err.message, true); }
  };
}

/* ---------------- contacts ---------------- */
async function renderContacts(view) {
  view.innerHTML = `
    <button class="btn" style="margin-bottom:12px" id="new-contact">+ إضافة عميل / مورد</button>
    <div class="list" id="c-list"></div>`;
  $("#new-contact").onclick = contactForm;
  const list = $("#c-list");
  const contacts = state.contacts;
  if (!contacts.length) { list.innerHTML = `<div class="empty">لا توجد جهات اتصال</div>`; return; }
  contacts.forEach((c) => {
    const kindLabel = { customer: "عميل", supplier: "مورد", both: "عميل ومورد" }[c.kind] || c.kind;
    const item = el(`
      <div class="item">
        <div class="main">
          <div class="title">${esc(c.name)}</div>
          <div class="sub">${kindLabel}${c.phone ? " · " + esc(c.phone) : ""}</div>
        </div>
        <div class="amount ${num(c.balance) >= 0 ? "pos" : "neg"}">${fmt(c.balance)}</div>
      </div>`);
    item.onclick = () => showStatement(c.id);
    list.append(item);
  });
}

function contactForm() {
  const form = el(`
    <form>
      <label>الاسم</label><input id="c-name" required />
      <label>النوع</label>
      <select id="c-kind"><option value="customer">عميل</option><option value="supplier">مورد</option><option value="both">عميل ومورد</option></select>
      <label>الهاتف</label><input id="c-phone" />
      <label>البريد</label><input id="c-email" type="email" />
      <label>الرقم الضريبي</label><input id="c-tax" />
      <label>العنوان</label><textarea id="c-addr"></textarea>
      <button class="btn" type="submit">حفظ</button>
    </form>`);
  const closeSheet = sheet("جهة اتصال جديدة", form);
  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/contacts", {
        method: "POST",
        body: JSON.stringify({
          name: $("#c-name", form).value,
          kind: $("#c-kind", form).value,
          phone: $("#c-phone", form).value,
          email: $("#c-email", form).value,
          tax_number: $("#c-tax", form).value,
          address: $("#c-addr", form).value,
        }),
      });
      closeSheet();
      await refreshLookups();
      toast("تم الحفظ");
      render();
    } catch (err) { toast(err.message, true); }
  };
}

async function showStatement(id) {
  const data = await api(`/contacts/${id}/statement`);
  const body = el(`
    <div>
      <div class="total-row"><span>الرصيد الحالي</span><b>${fmt(data.balance)}</b></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>التاريخ</th><th>البيان</th><th class="num">مدين</th><th class="num">دائن</th><th class="num">رصيد</th></tr></thead>
          <tbody>
            ${data.rows.length ? data.rows.map((r) => `<tr><td>${esc(r.date)}</td><td>${esc(r.description)}</td><td class="num">${fmt(r.debit)}</td><td class="num">${fmt(r.credit)}</td><td class="num">${fmt(r.balance)}</td></tr>`).join("")
              : `<tr><td colspan="5" class="empty">لا توجد حركات</td></tr>`}
          </tbody>
        </table>
      </div>
    </div>`);
  sheet("كشف حساب: " + data.contact.name, body);
}

/* ---------------- reports ---------------- */
async function renderReports(view) {
  view.innerHTML = `
    <div class="list">
      <div class="item" data-r="income"><div class="main"><div class="title">قائمة الدخل</div><div class="sub">الإيرادات والمصروفات وصافي الربح</div></div><div class="amount">›</div></div>
      <div class="item" data-r="balance"><div class="main"><div class="title">الميزانية العمومية</div><div class="sub">الأصول والالتزامات وحقوق الملكية</div></div><div class="amount">›</div></div>
      <div class="item" data-r="trial"><div class="main"><div class="title">ميزان المراجعة</div><div class="sub">أرصدة كل الحسابات</div></div><div class="amount">›</div></div>
      <div class="item" data-r="aging"><div class="main"><div class="title">أعمار الديون</div><div class="sub">أرصدة العملاء حسب التأخر</div></div><div class="amount">›</div></div>
      <div class="item" data-r="journal"><div class="main"><div class="title">دفتر اليومية</div><div class="sub">كل القيود المحاسبية</div></div><div class="amount">›</div></div>
    </div>`;
  view.querySelectorAll("[data-r]").forEach((n) => (n.onclick = () => showReport(n.dataset.r)));
}

async function showReport(kind) {
  const body = el(`<div class="empty">جاري التحميل…</div>`);
  sheet("تقرير", body);
  const box = body.parentElement ? body : null;
  try {
    if (kind === "income") {
      const d = await api("/reports/income-statement");
      body.innerHTML = `
        <div class="total-row"><span>الإيرادات</span><b>${fmt(d.income)}</b></div>
        <div class="total-row"><span>المصروفات</span><b>${fmt(d.expense)}</b></div>
        <div class="total-row grand"><span>صافي الربح</span><b class="${num(d.net_profit) < 0 ? "neg" : "pos"}">${fmt(d.net_profit)}</b></div>`;
    } else if (kind === "balance") {
      const d = await api("/reports/balance-sheet");
      body.innerHTML = `
        <div class="total-row"><span>الأصول</span><b>${fmt(d.assets)}</b></div>
        <div class="total-row"><span>الالتزامات</span><b>${fmt(d.liabilities)}</b></div>
        <div class="total-row"><span>حقوق الملكية</span><b>${fmt(d.total_equity)}</b></div>
        <div class="total-row grand"><span>الالتزامات + الملكية</span><b>${fmt(d.total_liabilities_equity)}</b></div>
        <div class="total-row"><span>متوازنة؟</span><b>${d.balanced ? "✓ نعم" : "✗ لا"}</b></div>`;
    } else if (kind === "trial") {
      const d = await api("/reports/trial-balance");
      body.innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>الحساب</th><th class="num">مدين</th><th class="num">دائن</th></tr></thead>
        <tbody>${d.rows.map((r) => `<tr><td>${esc(r.code + " " + r.name)}</td><td class="num">${fmt(r.debit)}</td><td class="num">${fmt(r.credit)}</td></tr>`).join("")}</tbody>
        <tfoot><tr><td>الإجمالي</td><td class="num">${fmt(d.total_debit)}</td><td class="num">${fmt(d.total_credit)}</td></tr></tfoot>
      </table></div>`;
    } else if (kind === "aging") {
      const d = await api("/reports/ar-aging");
      body.innerHTML = `
        <div class="total-row"><span>جاري</span><b>${fmt(d.buckets.current)}</b></div>
        <div class="total-row"><span>1-30 يوم</span><b>${fmt(d.buckets["1_30"])}</b></div>
        <div class="total-row"><span>31-60 يوم</span><b>${fmt(d.buckets["31_60"])}</b></div>
        <div class="total-row"><span>+60 يوم</span><b>${fmt(d.buckets["60_plus"])}</b></div>
        <div class="table-wrap" style="margin-top:10px"><table>
          <thead><tr><th>العميل</th><th class="num">الإجمالي</th></tr></thead>
          <tbody>${d.contacts.length ? d.contacts.map((c) => `<tr><td>${esc(c.name)}</td><td class="num">${fmt(c.total)}</td></tr>`).join("") : `<tr><td colspan="2" class="empty">لا مديونيات</td></tr>`}</tbody>
        </table></div>`;
    } else {
      const entries = await api("/reports/journal?limit=60");
      body.innerHTML = entries.length ? entries.map((e) => `
        <div class="card" style="margin-bottom:10px">
          <div class="sub">${esc(e.date)} · ${esc(e.reference || e.source_type)}</div>
          <div class="table-wrap"><table>
            <tbody>${e.lines.map((l) => `<tr><td>${esc(l.account)}</td><td class="num">${num(l.debit) ? fmt(l.debit) : ""}</td><td class="num">${num(l.credit) ? fmt(l.credit) : ""}</td></tr>`).join("")}</tbody>
          </table></div>
        </div>`).join("") : `<div class="empty">لا توجد قيود</div>`;
    }
  } catch (e) {
    body.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

/* ---------------- more ---------------- */
async function renderMore(view) {
  const s = state.settings;
  view.innerHTML = `
    <div class="card">
      <h3>بيانات الشركة</h3>
      <label>اسم الشركة</label><input id="s-name" value="${esc(s.company_name || "")}" />
      <label>العملة</label><input id="s-currency" value="${esc(s.currency || "")}" />
      <label>نسبة ضريبة القيمة المضافة %</label><input type="number" id="s-vat" value="${esc(s.vat_rate || "0")}" />
      <button class="btn" id="save-settings">حفظ الإعدادات</button>
    </div>
    <div class="card">
      <h3>دليل الحسابات</h3>
      <button class="btn ghost" id="open-accounts">عرض الحسابات</button>
      <button class="btn ghost" id="new-account">+ حساب جديد</button>
      <button class="btn ghost" id="new-journal">+ قيد يدوي</button>
    </div>
    <div class="card">
      <h3>الحساب</h3>
      <div class="total-row"><span>المستخدم</span><b>${esc(state.user?.full_name || "")}</b></div>
      <button class="btn danger" id="logout2">تسجيل الخروج</button>
    </div>`;
  $("#save-settings").onclick = async () => {
    try {
      state.settings = await api("/settings", {
        method: "PUT",
        body: JSON.stringify({ company_name: $("#s-name").value, currency: $("#s-currency").value, vat_rate: $("#s-vat").value }),
      });
      toast("تم الحفظ");
    } catch (e) { toast(e.message, true); }
  };
  $("#open-accounts").onclick = showAccounts;
  $("#new-account").onclick = accountForm;
  $("#new-journal").onclick = journalForm;
  $("#logout2").onclick = logout;
}

async function showAccounts() {
  const accounts = await api("/accounts");
  const body = el(`<div class="table-wrap"><table>
    <thead><tr><th>الكود</th><th>الحساب</th><th class="num">الرصيد</th></tr></thead>
    <tbody>${accounts.map((a) => `<tr><td>${esc(a.code)}</td><td>${esc(a.name_ar || a.name)}</td><td class="num">${fmt(a.balance)}</td></tr>`).join("")}</tbody>
  </table></div>`);
  sheet("دليل الحسابات", body);
}

function accountForm() {
  const types = [["asset", "أصول"], ["liability", "التزامات"], ["equity", "حقوق ملكية"], ["income", "إيرادات"], ["expense", "مصروفات"]];
  const groups = state.accounts.filter((a) => a.is_group);
  const form = el(`
    <form>
      <label>الكود</label><input id="a-code" required />
      <label>الاسم (عربي)</label><input id="a-name-ar" required />
      <label>الاسم (إنجليزي)</label><input id="a-name" />
      <label>النوع</label><select id="a-type">${types.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <label>الحساب الأب (اختياري)</label>
      <select id="a-parent"><option value="">— بدون —</option>${groups.map((a) => `<option value="${a.id}">${esc(a.code + " " + (a.name_ar || a.name))}</option>`).join("")}</select>
      <button class="btn" type="submit">حفظ الحساب</button>
    </form>`);
  const closeSheet = sheet("حساب جديد", form);
  form.onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/accounts", {
        method: "POST",
        body: JSON.stringify({
          code: $("#a-code", form).value,
          name: $("#a-name", form).value || $("#a-name-ar", form).value,
          name_ar: $("#a-name-ar", form).value,
          type: $("#a-type", form).value,
          parent_id: $("#a-parent", form).value ? Number($("#a-parent", form).value) : null,
          is_group: false,
        }),
      });
      closeSheet();
      await refreshLookups();
      toast("تم الحفظ");
    } catch (err) { toast(err.message, true); }
  };
}

function journalForm() {
  const accounts = state.accounts.filter((a) => !a.is_group);
  const contacts = state.contacts;
  const form = el(`
    <form>
      <label>التاريخ</label><input type="date" id="j-date" value="${today()}" required />
      <label>البيان</label><input id="j-memo" />
      <label>البنود (مدين = دائن)</label>
      <div id="j-lines"></div>
      <button type="button" class="btn ghost sm" id="j-add" style="width:100%">+ بند</button>
      <div class="total-row"><span>إجمالي المدين</span><b id="j-dr">0.00</b></div>
      <div class="total-row"><span>إجمالي الدائن</span><b id="j-cr">0.00</b></div>
      <button class="btn" type="submit">ترحيل القيد</button>
    </form>`);
  const box = $("#j-lines", form);
  function addLine() {
    const wrap = el(`<div class="j-line" style="border-bottom:1px dashed var(--line); padding-bottom:8px; margin-bottom:8px">
      <select class="j-acc">${accounts.map((a) => `<option value="${a.id}">${esc(a.code + " " + (a.name_ar || a.name))}</option>`).join("")}</select>
      <select class="j-contact" style="margin-top:6px"><option value="">— بدون جهة —</option>${contacts.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select>
      <div class="row" style="margin-top:6px">
        <input type="number" step="0.01" class="j-dr" placeholder="مدين" value="0" />
        <input type="number" step="0.01" class="j-cr" placeholder="دائن" value="0" />
        <button type="button" class="del" style="flex:0 0 40px; background:var(--danger); color:#fff; border:0; border-radius:8px; cursor:pointer">✕</button>
      </div>
    </div>`);
    wrap.querySelector(".del").onclick = () => { wrap.remove(); recalc(); };
    wrap.querySelectorAll("input").forEach((i) => i.addEventListener("input", recalc));
    box.append(wrap);
    recalc();
  }
  function recalc() {
    let dr = 0, cr = 0;
    box.querySelectorAll(".j-line").forEach((w) => {
      dr += num($(".j-dr", w).value);
      cr += num($(".j-cr", w).value);
    });
    $("#j-dr", form).textContent = fmt(dr);
    $("#j-cr", form).textContent = fmt(cr);
  }
  $("#j-add", form).onclick = addLine;
  addLine(); addLine();
  const closeSheet = sheet("قيد يدوي", form);
  form.onsubmit = async (e) => {
    e.preventDefault();
    const lines = [];
    box.querySelectorAll(".j-line").forEach((w) => {
      const dr = num($(".j-dr", w).value), cr = num($(".j-cr", w).value);
      if (!dr && !cr) return;
      lines.push({
        account_id: Number($(".j-acc", w).value),
        contact_id: $(".j-contact", w).value ? Number($(".j-contact", w).value) : null,
        debit: dr,
        credit: cr,
      });
    });
    try {
      await api("/journal", {
        method: "POST",
        body: JSON.stringify({ date: $("#j-date", form).value, memo: $("#j-memo", form).value, reference: "", lines }),
      });
      closeSheet();
      toast("تم ترحيل القيد");
      render();
    } catch (err) { toast(err.message, true); }
  };
}

/* ---------------- events ---------------- */
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await login($("#username").value, $("#password").value);
  } catch (err) {
    toast(err.message, true);
  }
});
$("#logout-btn").addEventListener("click", logout);
document.querySelectorAll("nav.tabbar button").forEach((b) => (b.onclick = () => go(b.dataset.route)));

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

boot();
