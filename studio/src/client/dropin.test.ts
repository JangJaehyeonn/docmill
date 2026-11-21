import { describe, expect, it } from "vitest";
import { canDropOn, isCellRange } from "./dropin";

describe("isCellRange", () => {
  it("accepts ranges and single cells", () => {
    expect(isCellRange("A1:C16")).toBe(true);
    expect(isCellRange(" b2 ")).toBe(true);
  });
  it("rejects other text", () => {
    for (const bad of ["", "A", "A1:", "1:2"]) expect(isCellRange(bad)).toBe(false);
  });
});

describe("canDropOn", () => {
  it("allows pptx tabs", () => {
    expect(canDropOn("보고서.PPTX")).toBeNull();
  });
  it("explains why docx, hwp and xlsx are refused", () => {
    expect(canDropOn("a.docx")).toContain("워드·한글");
    expect(canDropOn("a.hwpx")).toContain("워드·한글");
    expect(canDropOn("b.xlsx")).toContain("PPT 탭에만");
  });
});
