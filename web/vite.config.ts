/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dashboard is served from the API's origin in production (see
// docs/dashboard.md). In development Vite proxies API calls to the backend so
// the browser still sees a single origin and first-party cookies.
const backend = process.env.COMMITGUARD_API_URL ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": { target: backend, changeOrigin: false },
      "/health": { target: backend, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    // Never inline assets as data: URIs; the Content-Security-Policy only allows
    // fonts and scripts from the dashboard's own origin.
    assetsInlineLimit: 0,
    target: "es2022",
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    restoreMocks: true,
    testTimeout: 15_000,
  },
});
