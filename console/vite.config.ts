/*
  Vite build for the Agon console.

  The output is served by Azure Static Web Apps under script-src 'self' and
  style-src 'self', so every byte must arrive as an external file:
  - assetsInlineLimit 0: no data: URI for a font or an image, which
    font-src 'self' would refuse;
  - modulePreload polyfill off: it is a script the app does not need;
  - third-party code goes to one vendor chunk, so the static checker can hold
    the app chunk to zero DOM sinks and the vendor chunk to a pinned list.
*/
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ isSsrBuild }) => ({
  base: "/",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: isSsrBuild ? "dist-ssr" : "dist",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    modulePreload: { polyfill: false },
    sourcemap: false,
    rollupOptions: isSsrBuild
      ? {}
      : {
          output: {
            manualChunks(id: string) {
              return id.includes("node_modules") ? "vendor" : undefined;
            },
          },
        },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
}));
