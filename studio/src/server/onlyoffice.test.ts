import { mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  buildEditorConfig,
  signPath,
  verifyPathSig,
  documentKey,
  isOnlyofficeFile,
  normalizeUrl,
  rebaseDownloadUrl,
  replaceFile,
  shouldSave,
} from "./onlyoffice";

describe("onlyoffice helpers", () => {
  it("knows which files use the editor", () => {
    expect(isOnlyofficeFile("a/b.XLSX")).toBe(true);
    expect(isOnlyofficeFile("a.hwp")).toBe(false);
  });

  it("normalizes the server url", () => {
    expect(normalizeUrl("http://localhost/")).toBe("http://localhost");
    expect(normalizeUrl("")).toBeNull();
    expect(normalizeUrl(undefined)).toBeNull();
  });

  it("changes the document key when the file changes", () => {
    const a = documentKey("a.xlsx", 1, 10);
    expect(a).toBe(documentKey("a.xlsx", 1, 10));
    expect(a).not.toBe(documentKey("a.xlsx", 2, 10));
    expect(a).toMatch(/^[0-9a-f]+$/);
  });

  it("builds the editor config with file and callback urls", () => {
    const cfg = buildEditorConfig("dir/매출.xlsx", "k", "http://127.0.0.1:5000");
    expect(cfg.documentType).toBe("cell");
    expect(cfg.document.fileType).toBe("xlsx");
    expect(cfg.document.url).toBe(
      `http://127.0.0.1:5000/onlyoffice/file?path=${encodeURIComponent("dir/매출.xlsx")}`,
    );
    expect(cfg.editorConfig.callbackUrl).toContain("/onlyoffice/callback?path=");
    expect(() => buildEditorConfig("a.hwp", "k", "http://x")).toThrow();
  });

  it("signs paths and verifies signatures", () => {
    const sig = signPath("secret", "a.xlsx");
    expect(verifyPathSig("secret", "a.xlsx", sig)).toBe(true);
    expect(verifyPathSig("secret", "b.xlsx", sig)).toBe(false);
    expect(verifyPathSig("other", "a.xlsx", sig)).toBe(false);
    expect(verifyPathSig("secret", "a.xlsx", null)).toBe(false);
    expect(verifyPathSig("secret", "a.xlsx", "zz")).toBe(false);
    const cfg = buildEditorConfig("a.xlsx", "k", "http://h:1", sig);
    expect(cfg.document.url).toContain(`&sig=${sig}`);
    expect(cfg.editorConfig.callbackUrl).toContain(`&sig=${sig}`);
  });

  it("saves only on status 2 or 6", () => {
    expect([1, 2, 3, 4, 6].map(shouldSave)).toEqual([false, true, false, false, true]);
  });

  it("rebases download urls onto the configured server", () => {
    expect(rebaseDownloadUrl("http://internal/cache/f?x=1", "http://localhost")).toBe(
      "http://localhost/cache/f?x=1",
    );
    expect(rebaseDownloadUrl("http://localhost/cache/f", "http://localhost")).toBe(
      "http://localhost/cache/f",
    );
  });

  it("replaces a file's contents", async () => {
    const dir = mkdtempSync(path.join(tmpdir(), "studio-oo-"));
    const target = path.join(dir, "a.xlsx");
    writeFileSync(target, "old");
    await replaceFile(target, Buffer.from("new"));
    expect(readFileSync(target, "utf-8")).toBe("new");
    expect(readdirSync(dir)).toEqual(["a.xlsx"]);
  });

  it("handles overlapping replacements without leftovers", async () => {
    const dir = mkdtempSync(path.join(tmpdir(), "studio-oo-"));
    const target = path.join(dir, "a.xlsx");
    writeFileSync(target, "old");
    await Promise.all([replaceFile(target, Buffer.from("one")), replaceFile(target, Buffer.from("two"))]);
    expect(["one", "two"]).toContain(readFileSync(target, "utf-8"));
    expect(readdirSync(dir)).toEqual(["a.xlsx"]);
  });
});
