import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // dev convenience: /webhook/* -> local n8n
      "/webhook": { target: "http://localhost:5678", changeOrigin: true },
    },
  },
});
