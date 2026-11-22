import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { linkedFiles, linkStatus, parseRefresh, readVersions, sourcesInside } from "./links.js";

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

describe("readVersions", () => {
  it("collects recorded read versions per source file", () => {
    const text = JSON.stringify({
      numbers: [
        { source: { file: "/a.xlsx", read_version: "sha256:1" } },
        { source: { file: "/a.xlsx", read_version: "sha256:1" } },
        { source: { file: "/b.xlsx" } },
      ],
    });
    expect([...(readVersions(text).get("/a.xlsx") ?? [])]).toEqual(["sha256:1"]);
    expect(readVersions(text).has("/b.xlsx")).toBe(false);
    expect(readVersions("oops").size).toBe(0);
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

  it("marks a source changed after it was read as newer even if saved before the provenance", async () => {
    const src = path.join(root, "sales.xlsx");
    const doc = path.join(root, "report.pptx");
    const sha = (text: string) => `sha256:${createHash("sha256").update(text).digest("hex")}`;
    await fs.writeFile(doc, "p");
    await fs.writeFile(
      `${doc}.provenance.json`,
      JSON.stringify({ numbers: [{ value: 1, source: { file: src, read_version: sha("read") } }] }),
    );
    // the source was saved while inserting: older than the provenance file, but not what was read
    const old = new Date(Date.now() - 60_000);
    await fs.writeFile(src, "saved meanwhile");
    await fs.utimes(src, old, old);
    expect((await linkStatus(root, doc))[0]).toMatchObject({ path: "sales.xlsx", newer: true });
    await fs.writeFile(src, "read");
    await fs.utimes(src, old, old);
    expect((await linkStatus(root, doc))[0]).toMatchObject({ path: "sales.xlsx", newer: false });
  });

  it("does not guess between same-named sources in the workspace and the document folder", async () => {
    await fs.mkdir(path.join(root, "team"));
    const doc = path.join(root, "team", "report.pptx");
    await fs.writeFile(path.join(root, "sales.xlsx"), "top");
    await fs.writeFile(path.join(root, "team", "sales.xlsx"), "team");
    await fs.writeFile(doc, "p");
    await fs.writeFile(`${doc}.provenance.json`, prov(["sales.xlsx"]));
    expect(await linkStatus(root, doc)).toEqual([]);
    expect(await sourcesInside(root, doc)).toBe(false);
    await fs.rm(path.join(root, "sales.xlsx"));
    expect(await linkStatus(root, doc)).toMatchObject([{ path: "team/sales.xlsx" }]);
    expect(await sourcesInside(root, doc)).toBe(true);
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
