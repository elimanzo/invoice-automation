import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production bundle is committed under ../src/invoice_automation/static and served
// by FastAPI as static files (ADR-0008) — Node is only needed to rebuild it, never to run
// the system. `base: "./"` keeps asset URLs relative so the bundle works when served from
// any mount path.
// `npm run dev` for HMR while iterating on the front end: proxy every API route (the
// backend's actual surface, per api.py) to a `python -m invoice_automation.web`
// running on :8000, since the app fetches relative paths like `/runs` that Vite's own
// server has no route for. `/events` is SSE, so it needs `ws: false` (it isn't a
// websocket) but the proxy must not buffer the stream — `changeOrigin` alone covers
// both.
const API_ROUTES = ["/runs", "/reviews", "/status", "/impact", "/events"];

export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../src/invoice_automation/static",
    emptyOutDir: true,
  },
  server: {
    proxy: Object.fromEntries(
      API_ROUTES.map((route) => [route, { target: "http://127.0.0.1:8000", changeOrigin: true }])
    ),
  },
});
