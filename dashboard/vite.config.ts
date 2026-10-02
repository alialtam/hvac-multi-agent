import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

// The dashboard reads the example responses straight from ../contracts, so demo
// mode always matches the agreed API contract.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { "@contracts": path.resolve(__dirname, "../contracts") },
  },
  server: { port: 5173, fs: { allow: [".."] } },
  build: { chunkSizeWarningLimit: 1000 },
});
