/**
 * Bun serves the TypeScript app and proxies `/api` to the containerized backend,
 * so the browser only ever talks to one origin.
 */
import index from "./src/index.html";

const PORT = Number(Bun.env.PORT ?? 3000);
const BACKEND_URL = Bun.env.BACKEND_URL ?? "http://localhost:8000";

const server = Bun.serve({
  port: PORT,
  // Uploads are whole call recordings, and benchmark runs stream audio at 1x.
  idleTimeout: 255,
  routes: {
    "/api/*": (request) => {
      const url = new URL(request.url);
      const target = new URL(url.pathname + url.search, BACKEND_URL);
      const headers = new Headers(request.headers);
      headers.delete("host");
      return fetch(target, {
        method: request.method,
        headers,
        body: request.body,
        // @ts-expect-error - required by undici/Bun when streaming a request body
        duplex: "half",
      }).catch(
        (error: unknown) =>
          new Response(
            JSON.stringify({
              detail: `Backend unreachable at ${BACKEND_URL}: ${String(error)}`,
            }),
            { status: 502, headers: { "content-type": "application/json" } },
          ),
      );
    },
    "/*": index,
  },
});

console.log(`Frontend is running on http://localhost:${server.port}`);
