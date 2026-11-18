import { describe, expect, it } from "vitest";
import { baseName, changeLine, changedSources, markHandled } from "./links";

const src = (version: string, newer = true) => ({ path: "data/sales.xlsx", version, newer });

describe("changedSources", () => {
  it("suggests only sources saved after the document numbers", () => {
    expect(changedSources("r.pptx", [src("1", false)], {})).toEqual([]);
    expect(changedSources("r.pptx", [src("2")], {})).toEqual([src("2")]);
  });
  it("does not suggest a handled version again but does for a newer save", () => {
    const handled = markHandled("r.pptx", [src("2")], {});
    expect(changedSources("r.pptx", [src("2")], handled)).toEqual([]);
    expect(changedSources("r.pptx", [src("3")], handled)).toEqual([src("3")]);
    expect(changedSources("other.pptx", [src("2")], handled)).toEqual([src("2")]);
  });
});

describe("changeLine", () => {
  it("shows old and new values", () => {
    expect(changeLine({ label: "슬라이드 2 도형 3 · 합계", old: 10150, new: 10450 })).toBe(
      "슬라이드 2 도형 3 · 합계: 10,150 → 10,450",
    );
    expect(changeLine({ label: "비중", old: 39.9, new: 41.6 })).toBe("비중: 39.9 → 41.6");
  });
});

describe("baseName", () => {
  it("keeps the last path part", () => {
    expect(baseName("a/b/report.pptx")).toBe("report.pptx");
    expect(baseName("report.pptx")).toBe("report.pptx");
  });
});
