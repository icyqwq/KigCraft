/// <reference types="vitest" />

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:18000";
const buildDate = process.env.VITE_BUILD_DATE ?? new Date().toISOString();
const crossOriginIsolationHeaders = {
  "Cross-Origin-Embedder-Policy": "require-corp",
  "Cross-Origin-Opener-Policy": "same-origin",
} as const;
const immutableCacheHeader = "public, max-age=31536000, immutable";

function shouldLongCache(pathname = "") {
  return pathname.startsWith("/assets/") || pathname.startsWith("/models/") || pathname.startsWith("/mediapipe-wasm/");
}

function staticCacheHeadersPlugin(): Plugin {
  const middleware = (request: { url?: string }, response: { setHeader: (name: string, value: string) => void }, next: () => void) => {
    if (shouldLongCache(request.url?.split("?")[0] ?? "")) {
      response.setHeader("Cache-Control", immutableCacheHeader);
    }
    next();
  };

  return {
    name: "kig-static-cache-headers",
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
    },
    configureServer(server) {
      server.middlewares.use(middleware);
    },
  };
}

export default defineConfig({
  define: {
    __KIGCRAFT_BUILD_DATE__: JSON.stringify(buildDate),
  },
  plugins: [react(), staticCacheHeadersPlugin()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    // Allow LAN devices (phone on same Wi-Fi) to open the dev server by IP.
    allowedHosts: true,
    headers: crossOriginIsolationHeaders,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  preview: {
    headers: crossOriginIsolationHeaders,
  },
});

