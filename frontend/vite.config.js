import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Port 5173 is the one the backend's default CORS_ORIGINS allows.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  // MapLibre 6 starts its web worker from a file next to itself
  // (new URL("./maplibre-gl-worker.mjs", import.meta.url)). Pre-bundling
  // moves the library into .vite/deps without that file, and the map then
  // renders blank with "Worker failed to load".
  optimizeDeps: { exclude: ["maplibre-gl"] },
  worker: { format: "es" },
});
