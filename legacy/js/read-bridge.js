/**
 * Read bridge: this project's API -> the legacy screen state.
 *
 * The legacy screens read everything from one in-memory object (`S`) that
 * ``app.js`` owns, and ``app.js`` exposes three hooks for filling it:
 *
 *   window.__factoryGetState()          -> S
 *   window.__factoryReplaceArrays(patch) -> merge arrays into S and re-render
 *   window.__factoryPersistState(S)      -> persist (wired in persist.js)
 *
 * This module performs the read half: it calls our REST API, translates the
 * responses into the arrays the screens expect, and hands them to
 * ``__factoryReplaceArrays``. No diffing, no queue, no document store — the
 * relational database stays the single source of truth.
 *
 * Translation rules worth knowing:
 *   * Account ``k`` codes drive the legacy shortcuts (ak('inv'), ak('sales')...).
 *     We map them from the control account codes our backend already defines.
 *   * Balances come from our journal, grouped by account, so the trial balance
 *     the screens compute matches the backend exactly.
 *   * Ledger rows carry ``src``/``rid`` so the screens can link a row back to
 *     its document for drill-down.
 */
import { apiJson, loadBootstrap } from './supabase.js';

/** Account code -> legacy ``k`` tag, so ``ak('inv')`` finds the right account. */
const KIND_BY_CODE = {
  1010: 'cash',
  1020: 'cash',
  1100: 'ar',
  1200: 'inv',
  1210: 'waste',
  1220: 'wip',
  2100: 'ap',
  3100: 'cap',
  4100: 'sales',
  5100: 'cogs',
};

const DOC_TYPE_BY_KIND = { sales: 'sale', purchase: 'pur' };
const RETURN_TYPE_BY_KIND = { sales_return: 'sret', purchase_return: 'pret' };

const num = (v) => Number(v ?? 0);
const day = (v) => String(v ?? '').slice(0, 10);

/**
 * Legacy rows carry Arabic-Indic digits inside free-text names ("دولاب ٣ ضلفة").
 * The rest of the interface prints Latin digits, so normalise text at this
 * boundary rather than in every screen.
 */
const AR_DIGITS = '٠١٢٣٤٥٦٧٨٩';
const FA_DIGITS = '۰۱۲۳۴۵۶۷۸۹';
const txt = (v) =>
  String(v ?? '').replace(/[٠-٩۰-۹]/g, (ch) => {
    const ar = AR_DIGITS.indexOf(ch);
    return String(ar >= 0 ? ar : FA_DIGITS.indexOf(ch));
  });


/** Build ``S.accts`` from ``/accounts``. */
function mapAccounts(rows) {
  return rows.map((a) => ({
    id: a.id,
    code: a.code,
    name: txt(a.name_ar || a.name),
    name_en: a.name,
    t: { asset: 'A', liability: 'L', equity: 'Q', income: 'I', expense: 'X' }[a.type] || 'A',
    k: KIND_BY_CODE[Number(a.code)] || '',
    cash: Number(a.code) === 1010 || Number(a.code) === 1020 ? 1 : 0,
    cur: a.currency || 'EGP',
    parentId: a.parent_id ?? null,
    isGroup: !!a.is_group,
    active: a.is_active !== false,
  }));
}

/** Build ``S.wh`` from ``/inventory/warehouses``. */
function mapWarehouses(rows) {
  return rows.map((w) => ({
    id: w.id,
    name: txt(w.name_ar || w.name),
    code: w.code,
    parentId: w.parent_id ?? null,
    isGroup: !!w.is_group,
    kind: w.kind || '',
    active: w.is_active !== false,
  }));
}

/** Build ``S.items`` from the item catalogue, with stock layered on top.

The catalogue is the source of truth for which items exist: an item with no
stock on hand still has to appear in the pickers, so it cannot be derived from
``/inventory/stock`` alone. */
function mapItems(rows, stock, units) {
  const unitName = new Map((units || []).map((u) => [u.id, u.name_ar || u.name]));
  const byItem = new Map();

  for (const row of rows || []) {
    byItem.set(row.id, {
      id: row.id,
      name: txt(row.name_ar || row.name),
      sku: row.code,
      unit: unitName.get(row.base_unit_id) || 'قطعة',
      wh: 0,
      buy: num(row.purchase_price),
      retail: num(row.sale_price),
      wholesale: 0,
      img: '',
      model: '',
      st: {},
      val: 0,
      warehouses: {},
    });
  }

  for (const row of stock || []) {
    let item = byItem.get(row.item_id);
    if (!item) {
      item = {
        id: row.item_id,
        name: txt(row.item_name_ar || row.item_name),
        sku: row.item_code,
        unit: row.unit,
        wh: 0,
        buy: num(row.unit_cost),
        retail: 0,
        wholesale: 0,
        img: '',
        model: '',
        st: {},
        val: 0,
        warehouses: {},
      };
      byItem.set(row.item_id, item);
    }
    if (!item.wh) item.wh = row.warehouse_id;
    // The screens key stock by variant label ("colour / size"); a single
    // unvaried row uses "-", which is what the legacy seed used too.
    const label = [row.colour, row.size].filter(Boolean).join(' / ') || '-';
    item.st[label] = (item.st[label] || 0) + num(row.quantity);
    item.val += num(row.value);
    item.warehouses[row.warehouse_id] =
      (item.warehouses[row.warehouse_id] || 0) + num(row.quantity);
  }
  return [...byItem.values()];
}

/** Build ``S.codes`` from stock rows, one entry per item variant barcode. */
function mapCodes(stock) {
  const seen = new Set();
  const codes = [];
  for (const row of stock) {
    if (!row.variant_id || !row.variant_code) continue;
    const key = `${row.item_id}:${row.variant_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    codes.push({
      iid: row.item_id,
      vid: row.variant_id,
      code: row.variant_code,
      v: [row.colour, row.size].filter(Boolean).join(' / ') || '-',
    });
  }
  return codes;
}

/** Build ``S.partners`` from ``/contacts``. */
function mapPartners(rows) {
  return rows.map((c) => ({
    id: c.id,
    name: txt(c.name),
    phone: c.phone || '',
    c: c.kind === 'customer' || c.kind === 'both' ? 1 : 0,
    s: c.kind === 'supplier' || c.kind === 'both' ? 1 : 0,
    type: 'r',
    gov: '',
    city: '',
    adr: '',
    active: c.is_active !== false,
  }));
}

/** Build ``S.docs`` from ``/invoices``, flattening lines into the legacy shape. */
function mapInvoices(rows, items) {
  const itemById = new Map(items.map((i) => [i.id, i]));
  return rows.map((inv) => {
    const isReturn = !!inv.return_of_id;
    const legacyType = isReturn
      ? RETURN_TYPE_BY_KIND[inv.kind] || 'sret'
      : DOC_TYPE_BY_KIND[inv.kind] || 'sale';
    return {
      id: inv.id,
      no: inv.number,
      t: legacyType,
      pid: inv.contact_id,
      date: day(inv.issue_date),
      ts: inv.issue_date ? new Date(inv.issue_date).getTime() : Date.now(),
      due: day(inv.due_date),
      total: num(inv.total),
      sub: num(inv.subtotal),
      disc: num(inv.discount_total),
      tax: num(inv.tax_total),
      paid: num(inv.amount_paid),
      note: inv.notes || '',
      st: inv.status,
      originalId: inv.return_of_id ?? 0,
      lines: (inv.lines || []).map((l) => {
        const item = l.item_id ? itemById.get(l.item_id) : null;
        return {
          iid: l.item_id ?? 0,
          name: txt(item?.name || l.description || ''),
          v: '-',
          wh: l.warehouse_id ?? inv.warehouse_id ?? 0,
          qty: num(l.quantity),
          price: num(l.unit_price),
          disc: num(l.discount_rate),
          tax: num(l.tax_rate),
        };
      }),
    };
  });
}

/** Build ``S.vch`` from ``/payments``. */
function mapPayments(rows) {
  return rows.map((p) => ({
    id: p.id,
    no: p.number,
    dir: p.kind === 'receive' ? 'in' : 'out',
    title: p.kind === 'receive' ? 'سند قبض' : 'سند دفع',
    pid: p.contact_id,
    date: day(p.date),
    ts: p.date ? new Date(p.date).getTime() : Date.now(),
    total: num(p.amount),
    who: '',
    desc: p.reference || '',
    ac: p.bank_account_id ? [{ a: p.bank_account_id, m: num(p.amount) }] : [],
    ln: [],
    alloc: Object.fromEntries(
      (p.allocations || []).map((a) => [String(a.invoice_id), num(a.amount)]),
    ),
  }));
}

/** Build ``S.je`` and ``S.L`` from ``/journal``. */
function mapJournal(rows) {
  const je = [];
  const L = [];
  for (const entry of rows) {
    const ref = entry.reference || entry.id;
    je.push({
      id: entry.id,
      no: ref,
      date: day(entry.entry_date),
      ts: entry.entry_date ? new Date(entry.entry_date).getTime() : Date.now(),
      desc: entry.memo || '',
      src: entry.source_type || 'je',
      // Lets the UI match an auto-generated entry to the document that made it.
      sourceId: entry.source_id ?? 0,
    });
    for (const line of entry.lines || []) {
      L.push({
        d: day(entry.entry_date),
        ts: entry.entry_date ? new Date(entry.entry_date).getTime() : Date.now(),
        ref,
        desc: line.description || entry.memo || '',
        a: line.account_id,
        dr: num(line.debit),
        cr: num(line.credit),
        pid: line.contact_id ?? 0,
        src: entry.source_type || 'je',
        rid: entry.id,
      });
    }
  }
  return { je, L };
}

/** Build ``S.mv`` from ``/inventory/movements``. */
function mapMovements(rows) {
  return rows.map((m) => ({
    d: day(m.movement_date),
    ts: m.movement_date ? new Date(m.movement_date).getTime() : Date.now(),
    iid: m.item_id,
    wh: m.warehouse_id,
    v: '-',
    q: num(m.quantity),
    val: num(m.total_cost),
    ref: m.reference || '',
    pid: 0,
    kind: m.kind,
  }));
}

/** Build ``S.po`` from ``/production/work-orders``. */
function mapWorkOrders(rows) {
  const STATUS = { draft: 0, in_progress: 1, completed: 2, cancelled: 0 };
  return rows.map((o) => ({
    id: o.id,
    no: o.number,
    date: day(o.start_date),
    ts: o.start_date ? new Date(o.start_date).getTime() : Date.now(),
    due: day(o.due_date),
    st: STATUS[o.status] ?? 0,
    iid: o.item_id,
    iname: '',
    wh: o.warehouse_id,
    desc: o.notes || '',
    matCost: num(o.material_cost),
    wc: 0,
    x: [],
    mats: (o.materials || []).map((m) => ({
      iid: m.item_id,
      qty: num(m.quantity),
      wh: o.warehouse_id,
    })),
    subs: [],
    rec: [],
  }));
}

/** Build ``S.emp`` from ``/team``. */
function mapTeam(rows) {
  return rows.map((m) => ({
    id: m.id,
    name: txt(m.full_name || m.email),
    p1: m.email,
    p2: '',
    gov: '',
    city: '',
    adr: '',
    img: '',
    perm: [],
    role: m.role,
  }));
}

/** Settings -> the meta block the screens read (period, pricing, branding). */
function mapSettings(settings) {
  const logo = typeof settings.logo === 'string' ? settings.logo : '';
  return {
    name: txt(settings.company_name || 'MS-Factory'),
    logo: /^data:image\/(?:png|jpeg|webp);base64,/i.test(logo) ? logo : '',
    curs: [settings.currency || 'EGP'],
    pct: { w: 20, r: 40 },
    hide: [],
    biz: {
      tax_no: txt(settings.tax_number || ''),
      phone: txt(settings.phone || ''),
      address: txt(settings.address || ''),
    },
  };
}

/** Fetch every endpoint the read bridge needs, one request per collection. */
async function fetchEach() {
  const paths = {
    accounts: '/accounts',
    contacts: '/contacts',
    invoices: '/invoices',
    payments: '/payments',
    journal: '/journal',
    items: '/inventory/items',
    units: '/inventory/units',
    stock: '/inventory/stock',
    movements: '/inventory/movements',
    warehouses: '/inventory/warehouses',
    workOrders: '/production/work-orders',
    team: '/team',
    settings: '/settings',
  };
  const names = Object.keys(paths);
  const results = await Promise.all(names.map((n) => apiJson(paths[n])));
  const out = {};
  names.forEach((name, index) => {
    const { data, error } = results[index];
    if (error) throw error;
    out[name] = data;
  });
  return out;
}

/**
 * Fetch every collection the screens need.
 *
 * ``/bootstrap`` returns them all in one authenticated call, which matters
 * because the database is remote: each separate request paid for its own auth
 * round trip, and thirteen of them dominated startup. The per-collection
 * fallback stays for older deployments whose API predates the endpoint.
 */
async function fetchAll({ fresh = false } = {}) {
  const { data, error } = await loadBootstrap({ fresh });
  if (!error && data && Array.isArray(data.accounts)) {
    return {
      accounts: data.accounts,
      contacts: data.contacts || [],
      invoices: data.invoices || [],
      payments: data.payments || [],
      journal: data.journal || [],
      items: data.items || [],
      units: data.units || [],
      stock: data.stock || [],
      movements: data.movements || [],
      warehouses: data.warehouses || [],
      workOrders: data.work_orders || [],
      team: data.team || [],
      settings: data.settings ? { settings: data.settings } : { settings: {} },
    };
  }
  if (error && error.status && error.status !== 404 && error.status < 500) throw error;
  return fetchEach();
}

/**
 * Load everything and push it into the legacy state.
 *
 * ``fresh`` forces a new ``/bootstrap`` read; every caller that runs *after* a
 * write must pass it, or the cached startup payload would overwrite the change
 * the write just made.
 */
export async function hydrateLegacyState({ fresh = false } = {}) {
  const data = await fetchAll({ fresh });

  const accts = mapAccounts(data.accounts || []);
  const wh = mapWarehouses(data.warehouses || []);
  const items = mapItems(data.items || [], data.stock || [], data.units || []);
  const codes = mapCodes(data.stock || []);
  const partners = mapPartners(data.contacts || []);
  const docs = mapInvoices(data.invoices || [], items);
  const vch = mapPayments(data.payments || []);
  const { je, L } = mapJournal(data.journal || []);
  const mv = mapMovements(data.movements || []);
  const po = mapWorkOrders(data.workOrders || []);
  const emp = mapTeam(data.team || []);
  const meta = mapSettings(data.settings?.settings || {});

  window.__factoryReplaceArrays?.({
    accts, wh, items, partners, docs, vch, je, L, mv, po, emp, codes,
    wo: [],
    __meta: meta,
  });

  return { accts, wh, items, partners, docs, vch, je, L, mv, po, emp, codes };
}
