import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 화면은 out/client 로 빌드하고 server.js 가 HTTP로 내보낸다.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "out/client", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:5199" } },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
