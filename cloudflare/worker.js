import { DurableObject } from "cloudflare:workers";

// The container sleeps when no request arrives for this long, so an idle
// deployment costs nothing. Waking it again takes a few seconds.
const INACTIVITY_TIMEOUT_MS = 10 * 60 * 1000;

// The app binds to 8000 (see Dockerfile); Cloudflare routes to this port.
const APP_PORT = 8000;

// A cold start runs migrations against a remote database, which can take a
// while; 150 attempts at 400ms plus request time allows about two minutes.
const READY_ATTEMPTS = 150;

export class MSFactoryContainer extends DurableObject {
  #starting;

  async fetch(request) {
    // Concurrent requests must share one startup; starting twice throws.
    this.#starting ??= this.#startAndWait().finally(() => {
      this.#starting = undefined;
    });
    await this.#starting;

    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const forwarded = new Request(url, request);
    forwarded.headers.delete("host");
    return this.ctx.container.getTcpPort(APP_PORT).fetch(forwarded);
  }

  async #startAndWait() {
    const container = this.ctx.container;
    // Without these the container starts and then crashes on import, leaving
    // only a health-check timeout to debug. Fail with the missing name instead.
    const missing = ["MSFACTORY_DATABASE_URL", "MSFACTORY_SECRET_KEY"].filter(
      (name) => !this.env[name],
    );
    if (missing.length > 0) {
      throw new Error(`Missing Worker secrets: ${missing.join(", ")}`);
    }
    if (!container.running) {
      container.start({
        image: container.images.base,
        // "basic" is 1/4 vCPU and 1 GiB: enough for FastAPI plus one worker
        // process, and the cheapest size that avoids OOM on a cold start.
        instance: "basic",
        enableInternet: true,
        env: {
          MSFACTORY_DATABASE_URL: this.env.MSFACTORY_DATABASE_URL,
          MSFACTORY_SECRET_KEY: this.env.MSFACTORY_SECRET_KEY,
          MSFACTORY_ENVIRONMENT: "production",
          // Cloudflare terminates TLS in front of the container, so the app
          // must read the client address from the proxy header; otherwise
          // every user shares one rate-limit bucket.
          MSFACTORY_TRUST_PROXY_HEADERS: "true",
          PORT: String(APP_PORT),
        },
      });
    }
    await container.setInactivityTimeout(INACTIVITY_TIMEOUT_MS);

    const port = container.getTcpPort(APP_PORT);
    let lastError;
    for (let attempt = 0; attempt < READY_ATTEMPTS; attempt++) {
      try {
        const response = await port.fetch("http://container/health", {
          signal: AbortSignal.timeout(2000),
        });
        await response.body?.cancel();
        if (response.ok) return;
        lastError = new Error(`Health check returned ${response.status}`);
      } catch (error) {
        lastError = error;
      }
      await scheduler.wait(400);
    }
    throw new Error(`Container did not become healthy: ${lastError}`);
  }
}

export default {
  async fetch(request, env) {
    // A single instance runs one container. The name is constant rather than
    // per-user because sessions and data live in Postgres, so any instance can
    // serve any user, and one warm container beats many cold ones.
    return env.MSFACTORY_CONTAINER.getByName("main").fetch(request);
  },
};
