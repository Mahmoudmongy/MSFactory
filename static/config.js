/**
 * Deployment configuration, read before any other app module.
 *
 * Everything here is read once at load time so the same build can be served
 * either from the API's own origin or from a separate static host.
 *
 * ``apiBase`` defaults to the relative ``/api/v1``, used when FastAPI serves
 * these files itself. A static host (Cloudflare Pages,
 * Netlify, GitHub Pages) has no API next to it, so the backend's absolute URL
 * has to be supplied instead. Set it in ``api-config.js`` — a file the deploy
 * step writes, so no source file has to be edited per environment.
 */
(function () {
  'use strict';

  var DEFAULT_API_BASE = '/api/v1';

  function readOverride() {
    var value = window.MSFACTORY_API_BASE;
    if (typeof value !== 'string' || !value.trim()) return '';
    return value.trim().replace(/\/+$/, '');
  }

  var base = readOverride() || DEFAULT_API_BASE;

  window.MSFACTORY_CONFIG = {
    // Absolute when pointing at a remote backend, relative when co-hosted.
    apiBase: base,
    // True when the screens are served from a different origin than the API,
    // which is what makes the app cross-origin and needs CORS on the backend.
    isCrossOrigin: /^https?:\/\//i.test(base),
  };
})();
