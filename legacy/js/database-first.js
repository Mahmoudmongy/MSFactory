/**
 * Persistence bridge for the legacy screen set.
 *
 * Replaces the old record-batch synchroniser with a direct call to this
 * project's API. The relational database is the only source of truth: there is
 * no local queue, no version diffing and no document store.
 *
 * The legacy ``app.js`` calls ``window.__factoryPersistState(S)`` after every
 * change. For now this bridge persists nothing on write — the write half is
 * wired screen by screen in ``write-bridge.js`` — but it does load the real
 * data on startup so every screen shows this company's records.
 */
import { hydrateLegacyState } from './read-bridge.js';

window.__factoryDatabaseFirst = true;
window.__factoryDatabaseFirstFailed = false;

function setBootPhase(text) {
  window.__factorySetBootPhase?.(text);
}

async function boot() {
  // The inline bootstrap in index.html verifies the session first; wait for it
  // so we never read the API before we have a token.
  try {
    if (window.__factoryAuthPromise) await window.__factoryAuthPromise;
  } catch (_) {}

  if (window.__factoryAuthReady !== true) return;

  setBootPhase('جارٍ تحميل بيانات الشركة...');
  try {
    await hydrateLegacyState();
    window.__factoryDatabaseFirstReady = true;
    window.__factoryDatabaseFirstFailed = false;
    window.dispatchEvent(new CustomEvent('factory-database-ready'));
  } catch (error) {
    console.error('Legacy data load failed:', error);
    window.__factoryDatabaseFirstFailed = true;
    window.__showFactoryBootError?.(
      'تعذر تحميل بيانات الشركة',
      error?.message || 'تحقق من الاتصال ثم أعد المحاولة.',
    );
  }
}

// Until the write half is wired, saving is a no-op that resolves true so the
// screens do not block on it. The local state still updates immediately.
window.__factoryPersistState = async () => true;
window.__factoryDatabaseSyncNow = async () => {
  await hydrateLegacyState({ fresh: true });
};

boot();
