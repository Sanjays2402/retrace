import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const base = process.env.NEXT_PUBLIC_BASE_PATH ?? "/retrace";
export default defineConfig({
  plugins: [react()],
  base: `${base}/`,
  resolve: { alias: { "@": fileURLToPath(new URL(".", import.meta.url)) } },
  define: { "process.env.NEXT_PUBLIC_BASE_PATH": JSON.stringify(base) },
  build: { outDir: "dist/pages" },
});
