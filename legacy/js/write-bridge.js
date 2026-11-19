/**
 * Write bridge: the legacy screens -> this project's API.
 *
 * The read half fills the legacy state from our REST API. This half is the
 * reverse: each mutating screen function is wrapped so the change is written to
 * the relational database immediately, as a plain API call. There is no local
 * queue, no version diffing and no document store — the database is the only
 * source of truth, and the local arrays are a cache we refresh from the server
 * once a write succeeds.
 *
 * Id handling: the legacy screens generate large local ids (`uid()`), while the
 * database issues small serial ones. Rows created here keep their local id for
 * the current session and record the server id in `cloudId` — a field the legacy
 * code already reserved for exactly this. Everything sent to the API is
 * translated through `serverRef()` first, so a document can reference a partner
 * created a moment ago.
 */
import { apiJson } from './supabase.js';
import { hydrateLegacyState } from './read-bridge.js';

/** The database id behind a legacy row (local ids live in ``cloudId``). */
export function serverRef(entity) {
  if (!entity) return 0;
  return Number(entity.cloudId || entity.id || 0);
}

/** Re-read everything from the API so the cache matches the database again. */
async function refreshFromServer() {
  try {
    await hydrateLegacyState();
  } catch (error) {
    console.error('Refresh after write failed:', error);
  }
}

/** Serialises writes: one API round-trip at a time, then one refresh. */
let pending = Promise.resolve();

/**
 * Wrap one legacy save function so its effect reaches the database.
 *
 * ``watch`` names the state arrays the function may add to. The wrapper
 * snapshots them before the original runs and diffs them after, so it never
 * depends on the form's DOM. ``plan`` turns the added rows (and the edited row,
 * when ``args[0]`` is an id) into API calls.
 */
function wrapSave(name, { watch, plan }) {
  const original = window[name];
  if (typeof original !== 'function' || original.__factoryWrapped) return;

  const wrapped = function (...args) {
    const state = window.__factoryGetState?.() || {};
    const before = Object.fromEntries(watch.map((key) => [key, [...(state[key] || [])]]));

    const result = original.apply(this, args);

    const created = [];
    for (const key of watch) {
      const seen = new Set(before[key]);
      for (const row of state[key] || []) {
        if (!seen.has(row)) created.push({ key, row });
      }
    }

    const work = plan({ state, created, args, before });
    if (work && work.length) {
      pending = pending
        .then(() => runWork(name, work))
        .catch((error) => console.error(`Write bridge (${name}):`, error));
    }
    return result;
  };
  wrapped.__factoryWrapped = true;
  window[name] = wrapped;
}

async function runWork(name, work) {
  for (const { entity, request } of work) {
    const { data, error } = await request();
    if (error) {
      console.error(`Save failed (${name}):`, error);
      notify(error.message || 'تعذر حفظ التغييرات في قاعدة البيانات');
      return;
    }
    if (data && entity && data.id != null) entity.cloudId = String(data.id);
  }
  await refreshFromServer();
}

/**
 * ``toast`` is a top-level ``const`` in the legacy classic script, so it is not
 * a property of ``window`` — it lives in the global lexical scope and is still
 * reachable by name from a module.
 */
function notify(message) {
  try {
    if (typeof toast === 'function') return toast(message);
  } catch (_) {}
  console.warn(message);
}

const json = (path, method, body) => apiJson(path, { method, body: JSON.stringify(body) });

/** An ISO ``YYYY-MM-DD`` string from whatever date shape the screens hold. */
const day = (v) => String(v ?? '').slice(0, 10);

/** Contact kind from the legacy customer/supplier checkboxes. */
const contactKind = (row) => (row.c && row.s ? 'both' : row.c ? 'customer' : 'supplier');

/** The database id of a row the screens refer to by their own id. */
function serverIdOf(array, legacyId) {
  const row = (array || []).find((x) => String(x.id) === String(legacyId));
  return row ? serverRef(row) : 0;
}

/** Legacy document type -> API invoice kind. Returns ``null`` for returns. */
const INVOICE_KIND = { sale: 'sales', pur: 'purchase' };

/** Legacy account type letter -> API account type. */
const ACCOUNT_TYPE = { A: 'asset', L: 'liability', Q: 'equity', I: 'income', X: 'expense' };

/** The database unit id behind a legacy unit name (e.g. "قطعة"). */
async function unitIdFor(name) {
  const wanted = String(name || '').trim();
  if (!wanted) return null;
  const { data } = await apiJson('/inventory/units');
  const unit = (data || []).find((u) => u.name_ar === wanted || u.name === wanted);
  return unit ? unit.id : null;
}

/**
 * Turn a legacy document's lines into API invoice lines.
 *
 * The legacy form applies one discount to the whole document, while the API
 * discounts each line by a rate. Distributing the document discount across the
 * lines in proportion to their value gives the same invoice total.
 */
function invoiceLines(doc, items) {
  const gross = doc.lines.reduce((sum, l) => sum + (Number(l.qty) || 0) * (Number(l.price) || 0), 0);
  const docDiscount = Number(doc.disc) || 0;
  const docRate = gross > 0 ? (docDiscount / gross) * 100 : 0;

  return doc.lines
    .filter((l) => (Number(l.qty) || 0) > 0)
    .map((line) => {
      const itemId = serverIdOf(items, line.iid);
      const item = items.find((i) => String(i.id) === String(line.iid));
      // The API prices stock out of a warehouse, so an invoice line without one
      // has to fall back to the item's own default before it can be issued.
      const warehouseId =
        Number(line.warehouseId || doc.warehouseId || item?.wh) || null;
      return {
        description: line.name || '',
        quantity: Number(line.qty) || 0,
        unit_price: Number(line.price) || 0,
        discount_rate: (Number(line.disc) || 0) + docRate,
        tax_rate: Number(line.tax) || 0,
        item_id: itemId || null,
        warehouse_id: itemId ? warehouseId : null,
      };
    });
}

/** Install the wrappers. Safe to call more than once. */
export function installWriteBridge() {
  // --- customers and suppliers ---------------------------------------------
  wrapSave('saveP', {
    watch: ['partners'],
    plan: ({ state, created, args }) => {
      const id = args[0];
      if (id) {
        const row = (state.partners || []).find((p) => String(p.id) === String(id));
        if (!row) return [];
        return [
          {
            entity: row,
            request: () =>
              json(`/contacts/${serverRef(row)}`, 'PATCH', {
                name: row.name,
                kind: contactKind(row),
                phone: row.phone || '',
                address: row.adr || '',
              }),
          },
        ];
      }
      return created
        .filter((c) => c.key === 'partners')
        .map(({ row }) => ({
          entity: row,
          request: () =>
            json('/contacts', 'POST', {
              name: row.name,
              kind: contactKind(row),
              phone: row.phone || '',
              address: row.adr || '',
            }),
        }));
    },
  });

  // --- warehouses -----------------------------------------------------------
  wrapSave('addWh', {
    watch: ['wh'],
    plan: ({ created }) =>
      created
        .filter((c) => c.key === 'wh')
        .map(({ row }) => ({
          entity: row,
          request: () => json('/inventory/warehouses', 'POST', { name: row.name, name_ar: row.name }),
        })),
  });

  // --- sales and purchase invoices -----------------------------------------
  // The form removes the edited row and pushes a new one with the same id, so
  // an id that exists in the before-snapshot means this is an update.
  wrapSave('saveDoc', {
    watch: ['docs'],
    plan: ({ state, created, before }) => {
      const items = state.items || [];
      const partners = state.partners || [];
      const existingIds = new Set((before.docs || []).map((d) => String(d.id)));

      const write = (doc) => {
        const kind = INVOICE_KIND[doc.t];
        if (!kind) return null;
        const contactId = serverIdOf(partners, doc.pid);
        if (!contactId) {
          notify('اختر عميلًا أو موردًا محفوظًا في قاعدة البيانات');
          return null;
        }
        const payload = {
          kind,
          contact_id: contactId,
          issue_date: day(doc.date),
          notes: doc.note || '',
          lines: invoiceLines(doc, items),
        };
        return existingIds.has(String(doc.id))
          ? { entity: doc, request: () => json(`/invoices/${serverRef(doc)}`, 'PATCH', payload) }
          : { entity: doc, request: () => json('/invoices', 'POST', payload) };
      };

      return created
        .filter((c) => c.key === 'docs')
        .map(({ row }) => write(row))
        .filter(Boolean);
    },
  });

  // --- manual journal entries ----------------------------------------------
  wrapSave('saveJ', {
    watch: ['je'],
    plan: ({ state, created, before }) => {
      const accts = state.accts || [];
      const partners = state.partners || [];
      const lines = (state.L || []).filter((l) => l.src === 'je');

      const write = (entry) => {
        const rows = lines.filter((l) => String(l.rid) === String(entry.id));
        const journalLines = rows.map((row) => ({
          account_id: serverIdOf(accts, row.a),
          debit: Number(row.dr) || 0,
          credit: Number(row.cr) || 0,
          contact_id: row.pid ? serverIdOf(partners, row.pid) || null : null,
          description: row.desc || '',
        }));
        if (journalLines.length < 2) return null;
        return {
          entity: entry,
          request: () =>
            json('/journal', 'POST', {
              entry_date: day(entry.date),
              memo: entry.desc || '',
              lines: journalLines,
            }),
        };
      };

      return created
        .filter((c) => c.key === 'je')
        .map(({ row }) => write(row))
        .filter(Boolean);
    },
  });

  // --- items ----------------------------------------------------------------
  wrapSave('saveItem', {
    watch: ['items'],
    plan: ({ state, created, args }) => {
      const wh = state.wh || [];
      const existing = args[0]
        ? (state.items || []).find((i) => String(i.id) === String(args[0]))
        : null;
      const rows = existing
        ? [{ row: existing }]
        : created.filter((c) => c.key === 'items');

      return rows.map(({ row }) => ({
        entity: row,
        request: async () => {
          const payload = {
            code: row.sku || '',
            name: row.name,
            name_ar: row.name,
            purchase_price: Number(row.buy) || 0,
            sale_price: Number(row.retail) || 0,
            default_warehouse_id: serverIdOf(wh, row.wh) || null,
            base_unit_id: await unitIdFor(row.unit),
          };
          return existing
            ? json(`/inventory/items/${serverRef(row)}`, 'PATCH', payload)
            : json('/inventory/items', 'POST', payload);
        },
      }));
    },
  });

  // --- chart of accounts ----------------------------------------------------
  wrapSave('saveAcc', {
    watch: ['accts'],
    plan: ({ state, created }) => {
      const accts = state.accts || [];
      return created
        .filter((c) => c.key === 'accts')
        .map(({ row }) => {
          const parent = row.parentId
            ? accts.find((a) => String(a.id) === String(row.parentId))
            : null;
          return {
            entity: row,
            request: () =>
              json('/accounts', 'POST', {
                code: row.code || `9${accts.length}`,
                name: row.name,
                name_ar: row.name,
                type: ACCOUNT_TYPE[row.t] || 'expense',
                parent_id: parent ? serverRef(parent) : null,
                is_group: !!row.isGroup,
                currency: row.cur || 'EGP',
              }),
          };
        });
    },
  });

  wrapSave('saveAccName', {
    watch: ['accts'],
    plan: ({ state, created }) => {
      const accts = state.accts || [];
      return created
        .filter((c) => c.key === 'accts')
        .map(({ row }) => ({
          entity: row,
          request: () =>
            json(`/accounts/${serverRef(row)}`, 'PATCH', { name: row.name, name_ar: row.name }),
        }));
    },
  });

  // --- stock write-offs (هالك مخزون) ----------------------------------------
  // The record the screen keeps has no item or quantity, so the wrapper reads
  // them from the still-open form.
  wrapSave('saveWo', {
    watch: ['wo'],
    plan: ({ state, created }) => {
      const items = state.items || [];
      const warehouses = state.wh || [];

      return created
        .filter((c) => c.key === 'wo')
        .map(() => {
          const itemId = serverIdOf(items, $('#wi')?.value);
          const warehouseId = serverIdOf(warehouses, $('#ww')?.value);
          const quantity = Number($('#wq')?.value) || 0;
          const date = $('#wdt')?.value || day(new Date());
          const reason = $('#wd')?.value || '';

          if (!itemId || !warehouseId || quantity <= 0) {
            notify('الهالك يحتاج صنفًا ومخزنًا وكمية محفوظين في قاعدة البيانات');
            return null;
          }
          return {
            entity: created.find((c) => c.key === 'wo')?.row,
            request: () =>
              json('/inventory/movements/damage', 'POST', {
                item_id: itemId,
                warehouse_id: warehouseId,
                quantity,
                movement_date: day(date),
                reason,
              }),
          };
        })
        .filter(Boolean);
    },
  });

  // --- receipts and payments (سند قبض / سند دفع) -----------------------------
  // The API records one bank account and one party per voucher, so a voucher
  // split over several cash accounts is written as one payment per account.
  wrapSave('saveV', {
    watch: ['vch'],
    plan: ({ state, created }) => {
      const accts = state.accts || [];
      const partners = state.partners || [];

      return created
        .filter((c) => c.key === 'vch')
        .map(({ row }) => {
          const kind = row.dir === 'in' ? 'receive' : 'spend';
          const contactId = serverIdOf(partners, row.pid);
          const cashAccounts = (row.ac || [])
            .map((a) => ({ id: serverIdOf(accts, a.a), amount: Number(a.m) || 0 }))
            .filter((a) => a.id && a.amount > 0);

          if (!contactId || !cashAccounts.length) {
            notify('السند يحتاج طرفًا وحسابًا نقديًا محفوظين في قاعدة البيانات');
            return null;
          }
          return {
            entity: row,
            request: async () => {
              let last = { data: null, error: null };
              for (const account of cashAccounts) {
                last = await json('/payments', 'POST', {
                  kind,
                  contact_id: contactId,
                  date: day(row.date),
                  amount: account.amount,
                  bank_account_id: account.id,
                  reference: row.no || '',
                });
                if (last.error) return last;
              }
              return last;
            },
          };
        })
        .filter(Boolean);
    },
  });
}
