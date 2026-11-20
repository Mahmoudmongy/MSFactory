/**
 * Lifecycle actions: deactivate or delete a master record from the screens.
 *
 * The API refuses a hard delete as soon as the record has history (postings,
 * documents, stock movements), because deleting it would orphan rows that the
 * ledger still points at. So every action here tries the delete first and, on
 * 409, offers deactivation instead — the record then disappears from the
 * pickers while its past keeps its meaning.
 *
 * Exposed as window.__factoryLifecycle for the classic screens in app.js, which
 * cannot import modules.
 */
import { apiJson } from './supabase.js';
import { hydrateLegacyState } from './read-bridge.js';

const ENDPOINTS = {
  account: { path: '/accounts', label: 'الحساب' },
  contact: { path: '/contacts', label: 'الطرف' },
  warehouse: { path: '/inventory/warehouses', label: 'المخزن' },
};

function toastSafe(message) {
  try {
    if (typeof toast === 'function') return toast(message);
  } catch (_) {}
  console.warn(message);
}

async function refresh() {
  try {
    await hydrateLegacyState({ fresh: true });
  } catch (error) {
    console.error('Refresh after lifecycle change failed:', error);
  }
}

/** Deactivate or reactivate a record; returns true when the API accepted it. */
async function setActive(kind, id, active) {
  const spec = ENDPOINTS[kind];
  if (!spec) return false;
  const { error } = await apiJson(`${spec.path}/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ is_active: active }),
  });
  if (error) {
    toastSafe(error.message || 'تعذر تحديث حالة ' + spec.label);
    return false;
  }
  await refresh();
  toastSafe(active ? 'تم تفعيل ' + spec.label : 'تم إيقاف ' + spec.label);
  return true;
}

/**
 * Delete a record that never transacted. A 409 means the API is protecting
 * history, so the caller is offered deactivation instead of a dead end.
 */
async function remove(kind, id) {
  const spec = ENDPOINTS[kind];
  if (!spec) return false;
  const { error } = await apiJson(`${spec.path}/${id}`, { method: 'DELETE' });
  if (!error) {
    await refresh();
    toastSafe('تم حذف ' + spec.label);
    return true;
  }
  if (error.status === 409) {
    const ok = typeof confirm === 'function'
      ? confirm(error.message + '\n\nهل تريد إيقافه بدلًا من الحذف؟')
      : true;
    if (ok) return setActive(kind, id, false);
    return false;
  }
  toastSafe(error.message || 'تعذر حذف ' + spec.label);
  return false;
}

window.__factoryLifecycle = { setActive, remove };
