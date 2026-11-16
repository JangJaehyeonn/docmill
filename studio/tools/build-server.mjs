// 서버(src/server/index.ts)를 out/server.js 한 파일로 묶는다.
import { build } from "esbuild";

await build({
  entryPoints: ["src/server/index.ts"],
  bundle: true,
  platform: "node",
  target: "node22",
  format: "esm",
  outfile: "out/server.js",
  // 번들 안의 CommonJS 모듈이 require 를 쓸 수 있게 한다.
  banner: { js: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);" },
});
await build({
  entryPoints: ["electron/main.ts"],
  bundle: true,
  platform: "node",
  target: "node22",
  format: "cjs",
  external: ["electron"],
  outfile: "out/electron/main.cjs",
});
