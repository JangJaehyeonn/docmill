import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { parseSentences, sentenceRefs, sourceLabel } from "./sentences.js";

describe("sourceLabel", () => {
  it("shows sheet and range for cells", () => {
    expect(sourceLabel({ sheet: "매출", range: "C2:C16" })).toBe("매출!C2:C16");
    expect(sourceLabel({ range: "A2:A16" })).toBe("A2:A16");
  });
  it("shows a 1-based paragraph number for meeting notes", () => {
    expect(sourceLabel({ paragraph: 0 })).toBe("1번째 문단");
    expect(sourceLabel({ paragraph: 4 })).toBe("5번째 문단");
  });
  it("falls back when no location is recorded", () => {
    expect(sourceLabel({})).toBe("위치 정보 없음");
    expect(sourceLabel({ paragraph: -1 })).toBe("위치 정보 없음");
  });
});

describe("parseSentences", () => {
  it("keeps document order and drops malformed items", () => {
    const text = JSON.stringify({
      numbers: [],
      sentences: [
        { text: "매출 합계: 10", source: { file: "/a.xlsx", sheet: "S", range: "C2:C4" } },
        { text: "", source: { file: "/a.xlsx" } },
        { text: "no source" },
        { text: "할 일", source: { file: "/m.docx", paragraph: 2 } },
      ],
    });
    expect(parseSentences(text)).toEqual([
      { text: "매출 합계: 10", file: "/a.xlsx", label: "S!C2:C4" },
      { text: "할 일", file: "/m.docx", label: "3번째 문단" },
    ]);
  });
  it("returns empty for broken or missing sentences", () => {
    expect(parseSentences("oops")).toEqual([]);
    expect(parseSentences(JSON.stringify({ numbers: [] }))).toEqual([]);
  });
});

describe("sentenceRefs", () => {
  let root: string;
  let outside: string;
  beforeEach(async () => {
    root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "sentences-")));
    outside = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), "sentences-out-")));
  });
  afterEach(async () => {
    await fs.rm(root, { recursive: true, force: true });
    await fs.rm(outside, { recursive: true, force: true });
  });

  it("maps source files to workspace paths and hides outside ones", async () => {
    await fs.writeFile(path.join(root, "sales.xlsx"), "x");
    await fs.writeFile(path.join(outside, "secret.xlsx"), "x");
    const doc = path.join(root, "report.pptx");
    await fs.writeFile(doc, "x");
    const prov = {
      sentences: [
        { text: "a", source: { file: path.join(root, "sales.xlsx"), range: "C2:C4" } },
        { text: "b", source: { file: path.join(outside, "secret.xlsx"), range: "A1" } },
        { text: "c", source: { file: path.join(root, "missing.xlsx"), range: "A1" } },
      ],
    };
    await fs.writeFile(`${doc}.provenance.json`, JSON.stringify(prov));
    expect(await sentenceRefs(root, doc)).toEqual([
      { text: "a", source: "sales.xlsx", label: "C2:C4" },
      { text: "b", source: null, label: "A1" },
      { text: "c", source: null, label: "A1" },
    ]);
  });

  it("returns empty without a provenance file", async () => {
    expect(await sentenceRefs(root, path.join(root, "none.pptx"))).toEqual([]);
  });
});
