import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,          // bind 0.0.0.0 too, not just IPv6 ::1 — "localhost" can
                          // resolve to either depending on the machine, and a
                          // browser hitting 127.0.0.1 would otherwise get
                          // connection-refused even though the server is up
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  build: { outDir: "dist" },
});
