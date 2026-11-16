import { mkdtempSync, mkdirSync, utimesSync, writeFileSync } from "node:fs";
import type { AddressInfo } from "node:net";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createApp } from "./app";

let lastWorkspace = "";

function makeApp(extra: { onlyofficeUrl?: string } = {}): ReturnType<typeof createApp> {
  const base = mkdtempSync(path.join(tmpdir(), "studio-app-"));
  const workspace = path.join(base, "ws");
  lastWorkspace = workspace;
  const clientDir = path.join(base, "client");
  mkdirSync(workspace);
  mkdirSync(clientDir);
  writeFileSync(path.join(workspace, "a.xlsx"), "x");
  writeFileSync(path.join(clientDir, "index.html"), "<html></html>");
  return createApp({ workspace, clientDir, recentFile: path.join(base, "recent.json"),
    ...extra,
  });
}

function listen(server: ReturnType<typeof createApp>): Promise<number> {
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve((server.address() as AddressInfo).port));
  });
}

/** 클라이언트 라이브러리를 거치지 않고 요청 줄을 그대로 보내 상태 코드를 받는다. */
function rawStatus(port: number, target: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, "127.0.0.1", () => {
      socket.write(`GET ${target} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n`);
    });
    let data = "";
    socket.on("data", (chunk: Buffer) => (data += chunk.toString("utf-8")));
    socket.on("end", () => resolve(Number(/^HTTP\/1\.1 (\d{3})/.exec(data)?.[1])));
    socket.on("error", reject);
  });
}

describe("createApp", () => {
  let server: ReturnType<typeof createApp> | null = null;
  afterEach(() => {
    server?.close();
    server = null;
  });

  it("주소를 해석할 수 없는 요청에는 400을 돌려주고 서버는 계속 동작한다", async () => {
    server = makeApp();
    const port = await listen(server);
    expect(await rawStatus(port, "//")).toBe(400);
    expect(await rawStatus(port, "/api/files")).toBe(200);
  });

  it("변경 없이 닫힌 세션(status 4)은 키를 폐기해 다음 열기에 새 키를 준다", async () => {
    server = makeApp({ onlyofficeUrl: "http://localhost" });
    const port = await listen(server);
    const base = `http://127.0.0.1:${port}`;
    const getKey = async (): Promise<string> => {
      const r = await fetch(`${base}/api/editor-config?path=a.xlsx`);
      return ((await r.json()) as { config: { document: { key: string } } }).config.document.key;
    };
    const first = await getKey();
    expect(await getKey()).toBe(first);
    const cb = await fetch(`${base}/onlyoffice/callback?path=a.xlsx`, {
      method: "POST",
      body: JSON.stringify({ status: 4, key: first }),
    });
    expect(cb.status).toBe(200);
    // 외부 도구가 원본을 바꾼 상황.
    const target = path.join(lastWorkspace, "a.xlsx");
    writeFileSync(target, "changed-content");
    utimesSync(target, new Date(2030, 0, 1), new Date(2030, 0, 1));
    expect(await getKey()).not.toBe(first);
  });
});
