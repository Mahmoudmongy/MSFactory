/**
 * Auth bridge for the legacy screen set.
 *
 * The legacy screens were written against a hosted auth client. This module
 * exposes the same small surface they call — ``auth.getSession``,
 * ``auth.signInWithPassword``, ``auth.signOut`` and a ``from('profiles')``
 * lookup — but every call goes to this project's own API instead.
 *
 * Nothing here reaches an external service. The access and refresh tokens come
 * from ``/api/v1/auth/login`` and are kept in local storage under keys this
 * module owns.
 */
// Resolved at load time by js/config.js: relative when the API serves these
// files, absolute when the screens are hosted separately. Reading it from the
// global keeps this module free of build-time constants.
const API =
  (typeof window !== 'undefined' && window.MSFACTORY_CONFIG && window.MSFACTORY_CONFIG.apiBase) ||
  '/api/v1';

const ACCESS_KEY = 'msfactory.legacy.access';
const REFRESH_KEY = 'msfactory.legacy.refresh';

function readTokens() {
  try {
    const access = localStorage.getItem(ACCESS_KEY);
    const refresh = localStorage.getItem(REFRESH_KEY);
    return access ? { access, refresh } : null;
  } catch (_) {
    return null;
  }
}

function writeTokens(tokens) {
  try {
    localStorage.setItem(ACCESS_KEY, tokens.access);
    localStorage.setItem(REFRESH_KEY, tokens.refresh || '');
  } catch (_) {}
}

function clearTokens() {
  try {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  } catch (_) {}
}

export async function apiJson(path, options = {}) {
  const tokens = readTokens();
  const headers = { ...(options.headers || {}) };
  if (options.body) headers['Content-Type'] = 'application/json';
  if (tokens?.access) headers.Authorization = 'Bearer ' + tokens.access;
  let response;
  try {
    // Authenticated reads must never come from the HTTP cache: the API sends no
    // Cache-Control, so a re-read after a write could return the pre-write body.
    response = await fetch(API + path, { cache: 'no-store', ...options, headers });
  } catch (error) {
    return { data: null, error };
  }
  if (response.status === 204) return { data: null, error: null };
  let body = null;
  try {
    body = await response.json();
  } catch (_) {
    body = null;
  }
  if (!response.ok) {
    const detail = body?.detail;
    const message = (detail && (detail.message || detail)) || body?.message || 'request failed';
    const error = new Error(typeof message === 'string' ? message : 'request failed');
    error.status = response.status;
    return { data: null, error };
  }
  return { data: body, error: null };
}

/** Decode a JWT payload without verifying it — used only to read the expiry. */
function decodePayload(token) {
  try {
    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(decodeURIComponent(escape(atob(part))));
  } catch (_) {
    return null;
  }
}

let cachedMe = null;
let bootstrapPromise = null;

async function fetchMe() {
  const { data, error } = await apiJson('/auth/me');
  if (error) return { data: null, error };
  cachedMe = data;
  return { data, error: null };
}

/**
 * Load ``/bootstrap`` once per page load and share the result.
 *
 * Startup needs the caller's profile and every collection. Both used to be
 * separate authenticated requests, and against a remote database each one paid
 * its own round trip. The endpoint returns both, so the whole app now depends
 * on a single request; only the ``me`` half is cached here, the collections are
 * handed to the read bridge.
 *
 * Pass ``{ fresh: true }`` to bypass the cache. Callers that re-read state after
 * a write must do so, otherwise they re-apply the payload captured at startup
 * and the screen keeps showing the pre-write values.
 */
export function loadBootstrap({ fresh = false } = {}) {
  if (fresh) bootstrapPromise = null;
  if (!bootstrapPromise) {
    bootstrapPromise = apiJson('/bootstrap').then(({ data, error }) => {
      if (error) {
        bootstrapPromise = null;
        return { data: null, error };
      }
      if (data?.session) cachedMe = data.session;
      return { data, error: null };
    });
  }
  return bootstrapPromise;
}


/**
 * Build the profile shape the legacy screens expect.
 *
 * The legacy code treats an administrator as ``is_admin: true`` and reads a
 * ``permissions`` map of screen keys. Our API returns a role, so we translate
 * a role into the same screen map the old interface used.
 */
const SCREEN_KEYS = [
  'invoices', 'customers', 'suppliers', 'inventory', 'production',
  'reports', 'settings',
];

function profileFromMe(me) {
  const membership = me?.memberships?.[0];
  const role = membership?.role || 'viewer';
  const isAdmin = role === 'owner' || role === 'admin';
  const permissions = {};
  for (const key of SCREEN_KEYS) permissions[key] = isAdmin;
  if (!isAdmin) {
    // Staff can still reach the operational screens they own; the API remains
    // the real gate, this only shapes which menu entries render.
    permissions.invoices = true;
    permissions.customers = true;
    permissions.suppliers = true;
    permissions.inventory = true;
    permissions.production = true;
    permissions.reports = true;
  }
  return {
    id: String(me.user.id),
    created_by: String(me.user.id),
    is_admin: isAdmin,
    role,
    permissions,
    must_change_password: false,
    username: me.user.email,
    full_name: me.user.full_name || me.user.email,
  };
}

function userFromMe(me) {
  return { id: String(me.user.id), email: me.user.email };
}

export const supabase = {
  auth: {
    async getSession() {
      const tokens = readTokens();
      if (!tokens?.access) return { data: { session: null }, error: null };
      const payload = decodePayload(tokens.access);
      const expired = payload?.exp ? payload.exp * 1000 < Date.now() : false;
      if (expired && tokens.refresh) {
        const refreshed = await apiJson('/auth/refresh', {
          method: 'POST',
          body: JSON.stringify({ refresh_token: tokens.refresh }),
        });
        if (refreshed.error) {
          clearTokens();
          return { data: { session: null }, error: null };
        }
        writeTokens({
          access: refreshed.data.access_token,
          refresh: refreshed.data.refresh_token,
        });
      }
      const boot = await loadBootstrap();
      if (boot.error) {
        clearTokens();
        return { data: { session: null }, error: null };
      }
      const me = cachedMe;
      if (!me) {
        clearTokens();
        return { data: { session: null }, error: null };
      }
      return { data: { session: { user: userFromMe(me) } }, error: null };
    },

    async signInWithPassword({ email, password }) {
      const { data, error } = await apiJson('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
      });
      if (error) return { data: { user: null }, error };
      writeTokens({ access: data.access_token, refresh: data.refresh_token });
      bootstrapPromise = null;
      const boot = await loadBootstrap();
      const me = cachedMe;
      return { data: { user: me ? userFromMe(me) : null }, error: boot.error || (me ? null : new Error('profile unavailable')) };
    },

    async signOut() {
      const tokens = readTokens();
      if (tokens?.refresh) {
        await apiJson('/auth/logout', {
          method: 'POST',
          body: JSON.stringify({ refresh_token: tokens.refresh }),
        });
      }
      clearTokens();
      cachedMe = null;
      bootstrapPromise = null;
      return { error: null };
    },

    async getUser() {
      const me = await fetchMe();
      return { data: { user: me.data ? userFromMe(me.data) : null }, error: me.error };
    },

    async refreshSession() {
      const tokens = readTokens();
      if (!tokens?.refresh) return { error: new Error('no refresh token') };
      const { data, error } = await apiJson('/auth/refresh', {
        method: 'POST',
        body: JSON.stringify({ refresh_token: tokens.refresh }),
      });
      if (error) return { error };
      writeTokens({ access: data.access_token, refresh: data.refresh_token });
      bootstrapPromise = null;
      const me = await fetchMe();
      return {
        data: { session: { user: me.data ? userFromMe(me.data) : null } },
        error: me.error,
      };
    },
  },

  /**
   * Minimal query builder for the ``from(...)`` reads the legacy code makes.
   *
   * Real supabase-js returns a builder that is both chainable and awaitable, so
   * ``.maybeSingle()`` can still be followed by ``.abortSignal(...)``. We match
   * that: every method returns the same builder, and awaiting it resolves the
   * query. Only ``profiles`` has data; other tables resolve empty so the
   * auxiliary panels show "no records" instead of failing.
   */
  from(table) {
    const state = { single: false, count: false };
    const builder = {
      select(_cols, opts) {
        if (opts?.count) state.count = true;
        return builder;
      },
      eq() { return builder; },
      neq() { return builder; },
      gt() { return builder; },
      gte() { return builder; },
      lt() { return builder; },
      lte() { return builder; },
      in() { return builder; },
      is() { return builder; },
      order() { return builder; },
      limit() { return builder; },
      range() { return builder; },
      abortSignal() { return builder; },
      maybeSingle() { state.single = true; return builder; },
      single() { state.single = true; return builder; },
      then(resolve, reject) {
        const run = async () => {
          if (table !== 'profiles') {
            return state.count
              ? { data: null, count: 0, error: null }
              : { data: [], count: 0, error: null };
          }
          const me = cachedMe || (await fetchMe()).data;
          if (!me) return { data: null, error: new Error('not authenticated') };
          const profile = profileFromMe(me);
          if (state.single) return { data: profile, error: null };
          return { data: [profile], count: 1, error: null };
        };
        return run().then(resolve, reject);
      },
    };
    return builder;
  },

  // No realtime channel: the modern API is the source of truth and the legacy
  // screens re-read on demand.
  channel() {
    return { on() { return this; }, subscribe() { return this; } };
  },
  removeChannel() {},

  functions: {
    async invoke() {
      return { data: { success: false, error: 'غير متاح في هذا الإصدار' }, error: null };
    },
  },

  rpc: async () => ({ data: null, error: new Error('غير متاح في هذا الإصدار') }),
};

export default supabase;
