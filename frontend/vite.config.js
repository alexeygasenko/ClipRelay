import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const backend = process.env.VITE_BACKEND_URL || "http://127.0.0.1:8080";

function spaBypass(request) {
  if (
    request.method === "GET"
    && (request.headers.accept || "").includes("text/html")
  ) {
    return "/index.html";
  }
  return undefined;
}

const proxiedRoots = [
  "/api",
  "/media",
  "/youtube",
  "/spotify",
  "/settings",
  "/send",
  "/cancel",
  "/login",
  "/register",
  "/setup-admin",
  "/logout",
  "/admin",
  "/preview",
  "/static",
  "/download",
  "/tiktok",
];

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/frontend/" : "/",
  plugins: [vue()],
  build: {
    outDir: "../app/static/frontend",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      proxiedRoots.map((root) => [
        root,
        {
          target: backend,
          changeOrigin: true,
          bypass: spaBypass,
        },
      ]),
    ),
  },
}));
