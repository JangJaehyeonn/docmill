import { mkdtempSync, mkdirSync, utimesSync, writeFileSync } from "node:fs";
import type { AddressInfo } from "node:net";
import http from "node:http";
import net from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createApp, isLoopback } from "./app";

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
    const r = await fetch(`${base}/api/editor-config?path=a.xlsx`);
    const cfg = (await r.json()) as { config: { editorConfig: { callbackUrl: string } } };
    const cbPath = new URL(cfg.config.editorConfig.callbackUrl).search;
    const cb = await fetch(`${base}/onlyoffice/callback${cbPath}`, {
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

  it("문서 서버 주소(/onlyoffice/*)는 편집기 설정에 담긴 서명이 있어야 쓸 수 있다", async () => {
    server = makeApp({ onlyofficeUrl: "http://localhost" });
    const port = await listen(server);
    const base = `http://127.0.0.1:${port}`;
    const r = await fetch(`${base}/api/editor-config?path=a.xlsx`);
    const cfg = (await r.json()) as {
      config: { document: { url: string; key: string }; editorConfig: { callbackUrl: string } };
    };
    const fileQuery = new URL(cfg.config.document.url).search;
    expect(fileQuery).toContain("sig=");
    expect((await fetch(`${base}/onlyoffice/file${fileQuery}`)).status).toBe(200);
    expect((await fetch(`${base}/onlyoffice/file?path=a.xlsx`)).status).toBe(403);
    expect((await fetch(`${base}/onlyoffice/file?path=a.xlsx&sig=00`)).status).toBe(403);
    const forged = await fetch(`${base}/onlyoffice/callback?path=a.xlsx`, {
      method: "POST",
      body: JSON.stringify({ status: 6, key: cfg.config.document.key, url: "http://localhost/x" }),
    });
    expect(forged.status).toBe(403);
  });

  it.each([
    [1, 200, true],
    [0, 200, false],
    [3, 502, undefined],
  ])("강제 저장 결과 error %i에 %i를 돌려준다", async (code, status, unchanged) => {
    // 가짜 문서 서버: CommandService가 정해진 error 코드를 돌려준다.
    const fake = http.createServer((_req, res) => {
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify({ error: code }));
    });
    const fakePort = await new Promise<number>((resolve) =>
      fake.listen(0, "127.0.0.1", () => resolve((fake.address() as AddressInfo).port)),
    );
    try {
      server = makeApp({ onlyofficeUrl: `http://127.0.0.1:${fakePort}` });
      const port = await listen(server);
      const base = `http://127.0.0.1:${port}`;
      await fetch(`${base}/api/editor-config?path=a.xlsx`);
      const r = await fetch(`${base}/api/save?path=a.xlsx`, { method: "POST" });
      expect(r.status).toBe(status);
      if (status === 200) expect(await r.json()).toEqual({ ok: true, unchanged });
    } finally {
      fake.close();
    }
  });

  it("편집기가 열리지 않은 파일의 저장 요청은 409를 돌려준다", async () => {
    server = makeApp({ onlyofficeUrl: "http://127.0.0.1:1" });
    const port = await listen(server);
    const r = await fetch(`http://127.0.0.1:${port}/api/save?path=never-opened.xlsx`, { method: "POST" });
    expect(r.status).toBe(409);
  });

  it("루프백 주소만 앱 화면과 API를 쓸 수 있다", () => {
    expect(["127.0.0.1", "::1", "::ffff:127.0.0.1"].every(isLoopback)).toBe(true);
    expect(["172.17.0.2", "192.168.0.5", undefined].some(isLoopback)).toBe(false);
  });
});
