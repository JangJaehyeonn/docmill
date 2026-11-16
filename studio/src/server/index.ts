import path from "node:path";
import { fileURLToPath } from "node:url";
import { createApp } from "./app.js";
import { recentStorePath } from "./recent.js";

/** `--port <포트>` 인자를 읽는다 (없으면 0 = 빈 포트). */
export function parsePort(argv: string[]): number {
  const i = argv.indexOf("--port");
  const value = i >= 0 ? Number(argv[i + 1]) : 0;
  return Number.isInteger(value) && value >= 0 ? value : 0;
}

function main(): void {
  const workspace = path.resolve(process.env.DOCMILL_STUDIO_WORKSPACE || process.cwd());
  const here = path.dirname(fileURLToPath(import.meta.url));
  const publicHost = process.env.DOCMILL_STUDIO_PUBLIC_HOST || "127.0.0.1";
  const server = createApp({
    workspace,
    recentFile: recentStorePath(),
    clientDir: path.join(here, "client"),
    onlyofficeUrl: process.env.DOCMILL_ONLYOFFICE_URL,
    publicHost,
  });
  // 문서 서버가 다른 호스트 이름(예: host.docker.internal)으로 접근하면 모든 인터페이스에서 받는다.
  const bindHost = publicHost === "127.0.0.1" ? "127.0.0.1" : "0.0.0.0";
  server.listen(parsePort(process.argv.slice(2)), bindHost, () => {
    const addr = server.address();
    const port = typeof addr === "object" && addr ? addr.port : 0;
    // stdout 한 줄 계약: 앱이 준비되면 READY <url>
    process.stdout.write(`READY http://127.0.0.1:${port}/\n`);
  });
  const stop = (): void => {
    server.close(() => process.exit(0));
  };
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
