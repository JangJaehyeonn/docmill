import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createApp } from "./app";
import { isHwpFile, resolveRhwpPath } from "./rhwp";

describe("resolveRhwpPath", () => {
  it("빌드 폴더 안의 파일만 허용한다", () => {
    const dist = path.resolve("/tmp/dist");
    expect(resolveRhwpPath(dist, "/rhwp/")).toBe(path.join(dist, "index.html"));
    expect(resolveRhwpPath(dist, "/rhwp/assets/a.js")).toBe(path.join(dist, "assets", "a.js"));
    expect(resolveRhwpPath(dist, "/rhwp/../secret")).toBeNull();
    expect(resolveRhwpPath(dist, "/rhwp/%2e%2e/secret")).toBeNull();
    expect(resolveRhwpPath(dist, "/rhwp/%E0%A4%A")).toBeNull();
  });
});

describe("isHwpFile", () => {
  it("hwp·hwpx 만 한글 파일로 본다", () => {
    expect(isHwpFile("a.hwp")).toBe(true);
    expect(isHwpFile("dir/a.HWPX")).toBe(true);
    expect(isHwpFile("a.docx")).toBe(false);
  });
});

describe("rhwp 서버 경로", () => {
  let server: ReturnType<typeof createApp> | null = null;
  afterEach(() => {
    server?.close();
    server = null;
  });

  async function start(withDist = true): Promise<{ base: string; ws: string }> {
    const root = mkdtempSync(path.join(tmpdir(), "studio-rhwp-"));
    const ws = path.join(root, "ws");
    const dist = path.join(root, "dist");
    const clientDir = path.join(root, "client");
    for (const d of [ws, dist, clientDir]) mkdirSync(d);
    writeFileSync(path.join(ws, "a.hwp"), "old");
    writeFileSync(path.join(ws, "b.docx"), "docx");
    writeFileSync(path.join(clientDir, "index.html"), "<html></html>");
    writeFileSync(path.join(dist, "index.html"), "<html>rhwp</html>");
    writeFileSync(path.join(dist, "m.wasm"), "wasm");
    writeFileSync(path.join(root, "secret.txt"), "secret");
    server = createApp({
      workspace: ws,
      clientDir,
      recentFile: path.join(root, "recent.json"),
      rhwpDist: withDist ? dist : undefined,
    });
    const port = await new Promise<number>((resolve) => {
      server?.listen(0, "127.0.0.1", () => resolve((server?.address() as AddressInfo).port));
    });
    return { base: `http://127.0.0.1:${port}`, ws };
  }

  it("/rhwp/ 에서 빌드 파일을 내보내고 wasm 형식을 맞춘다", async () => {
    const { base } = await start();
    const page = await fetch(`${base}/rhwp/`);
    expect(await page.text()).toBe("<html>rhwp</html>");
    const wasm = await fetch(`${base}/rhwp/m.wasm`);
    expect(wasm.headers.get("content-type")).toBe("application/wasm");
    expect((await fetch(`${base}/rhwp/nope.js`)).status).toBe(404);
  });

  it("빌드 폴더 밖 파일은 내보내지 않는다", async () => {
    const { base } = await start();
    // fetch 는 주소를 정규화하므로 앱 화면으로 돌아올 수 있다. 어느 쪽이든 비밀 파일 내용은 없어야 한다.
    const res = await fetch(`${base}/rhwp/%2e%2e/secret.txt`);
    expect(await res.text()).not.toContain("secret");
  });

  it("빌드 폴더가 없으면 안내와 함께 404 를 준다", async () => {
    const { base } = await start(false);
    expect((await fetch(`${base}/rhwp/`)).status).toBe(404);
  });

  it("한글 파일을 읽고 저장하면 작업 폴더의 파일이 바뀐다", async () => {
    const { base, ws } = await start();
    const url = `${base}/api/hwp?path=a.hwp`;
    expect(await (await fetch(url)).text()).toBe("old");
    const put = await fetch(url, {
      method: "PUT",
      headers: { "content-type": "application/octet-stream" },
      body: new Uint8Array([1, 2, 3]),
    });
    expect(put.status).toBe(200);
    expect([...readFileSync(path.join(ws, "a.hwp"))]).toEqual([1, 2, 3]);
  });

  it("저장은 형식·크기·파일 종류를 확인한다", async () => {
    const { base } = await start();
    const url = `${base}/api/hwp?path=a.hwp`;
    const wrongType = await fetch(url, { method: "PUT", body: "x" });
    expect(wrongType.status).toBe(415);
    const empty = await fetch(url, {
      method: "PUT",
      headers: { "content-type": "application/octet-stream" },
      body: new Uint8Array(),
    });
    expect(empty.status).toBe(400);
    expect((await fetch(`${base}/api/hwp?path=b.docx`)).status).toBe(400);
    expect((await fetch(`${base}/api/hwp?path=../x.hwp`)).status).toBe(403);
    expect((await fetch(url, { method: "DELETE" })).status).toBe(405);
  });
});
