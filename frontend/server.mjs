import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";
import { extname, join, normalize, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const distDir = resolve(__dirname, "dist");
const port = Number(process.env.PORT ?? 5173);
const host = process.env.HOST ?? "0.0.0.0";
const apiProxyTarget = process.env.API_PROXY_TARGET ?? process.env.VITE_API_PROXY_TARGET ?? "http://api:8000";

const crossOriginIsolationHeaders = {
  "Cross-Origin-Embedder-Policy": "require-corp",
  "Cross-Origin-Opener-Policy": "same-origin",
};

const immutableCache = "public, max-age=31536000, immutable";
const htmlCache = "no-cache";
const mimeTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"],
  [".onnx", "application/octet-stream"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".tflite", "application/octet-stream"],
  [".wasm", "application/wasm"],
  [".webp", "image/webp"],
]);

function isLongCachePath(pathname) {
  return (
    pathname.startsWith("/assets/") ||
    pathname.startsWith("/models/") ||
    pathname.startsWith("/mediapipe-wasm/")
  );
}

function applySharedHeaders(response, pathname) {
  Object.entries(crossOriginIsolationHeaders).forEach(([name, value]) => response.setHeader(name, value));
  response.setHeader("X-Content-Type-Options", "nosniff");
  response.setHeader("Cache-Control", isLongCachePath(pathname) ? immutableCache : htmlCache);
}

function resolveStaticFile(pathname) {
  const decodedPath = decodeURIComponent(pathname);
  const normalizedPath = normalize(decodedPath).replace(/^(\.\.(\/|\\|$))+/, "");
  const relativePath = normalizedPath === sep ? "index.html" : normalizedPath.replace(/^[/\\]+/, "");
  let filePath = resolve(distDir, relativePath);

  if (!filePath.startsWith(distDir + sep) && filePath !== distDir) return null;
  if (existsSync(filePath) && statSync(filePath).isDirectory()) {
    filePath = join(filePath, "index.html");
  }
  if (existsSync(filePath) && statSync(filePath).isFile()) return filePath;

  return resolve(distDir, "index.html");
}

function serveStatic(request, response, pathname) {
  const filePath = resolveStaticFile(pathname);
  if (!filePath) {
    response.writeHead(404);
    response.end("Not found");
    return;
  }

  const extension = extname(filePath);
  applySharedHeaders(response, pathname);
  response.setHeader("Content-Type", mimeTypes.get(extension) ?? "application/octet-stream");
  response.setHeader("Content-Length", statSync(filePath).size);
  response.writeHead(200);

  if (request.method === "HEAD") {
    response.end();
    return;
  }

  createReadStream(filePath).pipe(response);
}

function proxyApi(request, response) {
  const target = new URL(apiProxyTarget);
  const requestPath = request.url?.replace(/^\/api/, "") || "/";
  const proxyRequest = (target.protocol === "https:" ? httpsRequest : httpRequest)(
    {
      headers: {
        ...request.headers,
        host: target.host,
      },
      hostname: target.hostname,
      method: request.method,
      path: requestPath,
      port: target.port || (target.protocol === "https:" ? 443 : 80),
      protocol: target.protocol,
    },
    (proxyResponse) => {
      response.writeHead(proxyResponse.statusCode ?? 502, proxyResponse.headers);
      proxyResponse.pipe(response);
    },
  );

  proxyRequest.on("error", () => {
    response.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("API proxy failed");
  });

  request.pipe(proxyRequest);
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
  if (url.pathname.startsWith("/api/") || url.pathname === "/api") {
    proxyApi(request, response);
    return;
  }

  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405, { Allow: "GET, HEAD" });
    response.end("Method not allowed");
    return;
  }

  serveStatic(request, response, url.pathname);
});

server.listen(port, host, () => {
  console.log(`Frontend static server listening on http://${host}:${port}`);
});
