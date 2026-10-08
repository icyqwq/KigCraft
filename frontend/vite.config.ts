/// <reference types="vitest" />

import react from "@vitejs/plugin-react";
import { defineConfig, normalizePath, type Plugin } from "vite";
import { configDefaults } from "vitest/config";
import { syncTokens, tokenSource } from "./scripts/design-tokens.mjs";

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

function designTokensPlugin(): Plugin {
  return {
    name: "kig-design-tokens",
    apply: "serve",
    async configureServer() {
      await syncTokens();
    },
    async handleHotUpdate(context) {
      if (normalizePath(context.file) === normalizePath(tokenSource)) await syncTokens();
    },
  };
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
  // Served at / by default; the side-by-side deployment builds with VITE_BASE=/v2/.
  base: process.env.VITE_BASE || "/",
  define: {
    __KIGCRAFT_BUILD_DATE__: JSON.stringify(buildDate),
  },
  plugins: [designTokensPlugin(), react(), staticCacheHeadersPlugin()],
  test: {
    exclude: [...configDefaults.exclude, "scripts/editor-boundaries.test.mjs"],
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
    // Allow LAN devices (phone on same Wi-Fi) to open the dev server by IP.
    allowedHosts: true,
    headers: crossOriginIsolationHeaders,
    // Editing a test must not reload the page someone is using (e.g. while an image is generating).
    watch: { ignored: ["**/*.test.ts", "**/*.test.tsx", "**/test/**"] },
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

