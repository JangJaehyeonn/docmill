import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { linkedFiles, linkStatus, parseRefresh } from "./links.js";

const prov = (files: string[]) =>
  JSON.stringify({ numbers: files.map((file) => ({ value: 1, source: { file } })) });

describe("linkedFiles", () => {
  it("lists source files once in order", () => {
    expect(linkedFiles(prov(["/a.xlsx", "/b.xlsx", "/a.xlsx"]))).toEqual(["/a.xlsx", "/b.xlsx"]);
  });
  it("returns empty for broken JSON", () => {
    expect(linkedFiles("oops")).toEqual([]);
    expect(linkedFiles(JSON.stringify({ numbers: "x" }))).toEqual([]);
  });
});

describe("linkStatus", () => {
  let root: string;
  beforeEach(async () => {
    root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "links-")));
  });
  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
  });

  it("marks a source saved after the provenance file as newer", async () => {
    const src = path.join(root, "sales.xlsx");
    const doc = path.join(root, "report.pptx");
    await fs.writeFile(src, "x");
    await fs.writeFile(doc, "p");
    await fs.writeFile(`${doc}.provenance.json`, prov([src, "/outside/other.xlsx"]));
    const old = new Date(Date.now() - 60_000);
    await fs.utimes(src, old, old);
    const before = await linkStatus(root, doc);
    expect(before).toHaveLength(1);
    expect(before[0]).toMatchObject({ path: "sales.xlsx", newer: false });
    await fs.writeFile(src, "changed");
    const after = await linkStatus(root, doc);
    expect(after[0]).toMatchObject({ path: "sales.xlsx", newer: true });
    expect(after[0]?.version).not.toBe(before[0]?.version);
  });

  it("returns empty without a provenance file", async () => {
    const doc = path.join(root, "plain.pptx");
    await fs.writeFile(doc, "p");
    expect(await linkStatus(root, doc)).toEqual([]);
  });
});

describe("parseRefresh", () => {
  it("reads changes, warnings and summary", () => {
    const out = parseRefresh(
      JSON.stringify({
        summary: "숫자 1곳",
        changes: [{ label: "합계", old: 1, new: 2, location: {} }, { label: "bad" }],
        warnings: ["w", 3],
      }),
    );
    expect(out).toEqual({ summary: "숫자 1곳", changes: [{ label: "합계", old: 1, new: 2 }], warnings: ["w"] });
  });
  it("reports tool errors and broken output", () => {
    expect(parseRefresh(JSON.stringify({ error: "원본 파일이 없다" })).error).toBe("원본 파일이 없다");
    expect(parseRefresh("oops").error).toBeTruthy();
  });
});
