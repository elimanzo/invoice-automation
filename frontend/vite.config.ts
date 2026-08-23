import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The production bundle is committed under ../src/invoice_automation/static and served
// by FastAPI as static files (ADR-0008) — Node is only needed to rebuild it, never to run
// the system. `base: "./"` keeps asset URLs relative so the bundle works when served from
// any mount path.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../src/invoice_automation/static",
    emptyOutDir: true,
  },
});
