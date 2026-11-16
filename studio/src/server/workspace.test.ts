import { mkdtempSync, mkdirSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { addRecent, listRecent } from "./recent";
import { listFiles, resolveInWorkspace, WorkspaceError } from "./workspace";

function setup(): { root: string; outside: string } {
  const base = mkdtempSync(path.join(tmpdir(), "studio-"));
  const root = path.join(base, "ws");
  mkdirSync(root);
  writeFileSync(path.join(root, "b.docx"), "x");
  writeFileSync(path.join(root, "a.xlsx"), "x");
  writeFileSync(path.join(root, "note.txt"), "x");
  const outside = path.join(base, "secret.docx");
  writeFileSync(outside, "x");
  return { root, outside };
}

describe("workspace", () => {
  it("lists only document files, sorted", async () => {
    const { root } = setup();
    expect(await listFiles(root)).toEqual(["a.xlsx", "b.docx"]);
  });

  it("skips symlinked directories that point outside the workspace", async () => {
    const { root, outside } = setup();
    const outsideDir = path.join(path.dirname(outside), "outside-dir");
    mkdirSync(outsideDir);
    writeFileSync(path.join(outsideDir, "leak.docx"), "x");
    symlinkSync(outsideDir, path.join(root, "linked"), "dir");
    mkdirSync(path.join(root, "sub"));
    writeFileSync(path.join(root, "sub", "c.pptx"), "x");
    expect(await listFiles(root)).toEqual(["a.xlsx", "b.docx", "sub/c.pptx"]);
  });

  it("resolves files inside the workspace", async () => {
    const { root } = setup();
    expect(await resolveInWorkspace(root, "a.xlsx")).toBe("a.xlsx");
  });

  it("rejects parent-directory and absolute paths outside", async () => {
    const { root, outside } = setup();
    await expect(resolveInWorkspace(root, "../secret.docx")).rejects.toThrow(WorkspaceError);
    await expect(resolveInWorkspace(root, outside)).rejects.toThrow("작업 폴더");
  });

  it("rejects symlinks that escape the workspace", async () => {
    const { root, outside } = setup();
    symlinkSync(outside, path.join(root, "link.docx"));
    await expect(resolveInWorkspace(root, "link.docx")).rejects.toThrow("작업 폴더");
  });

  it("rejects unsupported types", async () => {
    const { root } = setup();
    await expect(resolveInWorkspace(root, "note.txt")).rejects.toThrow("지원하지 않는");
  });
});

describe("recent", () => {
  it("puts the latest file on top without duplicates", async () => {
    const { root } = setup();
    const file = path.join(root, "..", "recent.json");
    await addRecent(file, root, "a.xlsx");
    await addRecent(file, root, "b.docx");
    await addRecent(file, root, "a.xlsx");
    expect(await listRecent(file, root)).toEqual(["a.xlsx", "b.docx"]);
    expect(await listRecent(file, "/other")).toEqual([]);
  });
});
