import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { applyPreview, cancelPreview, parsePreview, registerPreview, type PendingPreview } from "./preview.js";

async function setup(): Promise<{ root: string; orig: string; copy: string }> {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "preview-")));
  await fs.mkdir(path.join(root, ".docmill-preview"));
  const orig = path.join(root, "a.docx");
  const copy = path.join(root, ".docmill-preview", "a.docx");
  await fs.writeFile(orig, "old");
  await fs.writeFile(copy, "new");
  return { root, orig, copy };
}

describe("parsePreview", () => {
  it("modified_copy 가 있으면 요약을 만든다", () => {
    const info = parsePreview(
      JSON.stringify({ path: "/w/a.docx", modified_copy: "/w/.p/a.docx", changed_paragraphs: 2 }),
    );
    expect(info).toEqual({ path: "/w/a.docx", copy: "/w/.p/a.docx", summary: ["바뀐 문단: 2개"] });
  });

  it("modified_copy 가 없거나 JSON 이 아니면 null", () => {
    expect(parsePreview(JSON.stringify({ path: "/w/a.docx" }))).toBeNull();
    expect(parsePreview("not json")).toBeNull();
  });
});

describe("미리보기 적용·취소", () => {
  it("적용하면 원본이 사본 내용이 되고 사본이 사라진다", async () => {
    const { root, orig, copy } = await setup();
    const store = new Map<string, PendingPreview>();
    const reg = await registerPreview(store, root, { path: orig, copy, summary: [] });
    expect(reg).not.toBeNull();
    expect(await applyPreview(store, root, reg!.id)).toBe("a.docx");
    expect(await fs.readFile(orig, "utf-8")).toBe("new");
    await expect(fs.stat(copy)).rejects.toThrow();
    expect(await applyPreview(store, root, reg!.id)).toBeNull();
  });

  it("취소하면 원본은 그대로이고 사본이 사라진다", async () => {
    const { root, orig, copy } = await setup();
    const store = new Map<string, PendingPreview>();
    const reg = await registerPreview(store, root, { path: orig, copy, summary: [] });
    expect(await cancelPreview(store, root, reg!.id)).toBe(true);
    expect(await fs.readFile(orig, "utf-8")).toBe("old");
    await expect(fs.stat(copy)).rejects.toThrow();
  });

  it("작업 폴더 밖 사본이나 원본과 같은 파일은 등록하지 않는다", async () => {
    const { root, orig } = await setup();
    const store = new Map<string, PendingPreview>();
    expect(await registerPreview(store, root, { path: orig, copy: orig, summary: [] })).toBeNull();
    expect(await registerPreview(store, root, { path: orig, copy: "/etc/passwd", summary: [] })).toBeNull();
  });
});
