import { describe, expect, it } from "vitest";
import { isCellRange, parseInsert } from "./dropin.js";

describe("isCellRange", () => {
  it("accepts ranges and single cells", () => {
    expect(isCellRange("A1:C16")).toBe(true);
    expect(isCellRange(" b2 ")).toBe(true);
    expect(isCellRange("$A$1:$C$16")).toBe(true);
  });
  it("rejects other text", () => {
    for (const bad of ["", "A", "1:2", "A1:", "시트1!A1", "A1:C16; rm"]) expect(isCellRange(bad)).toBe(false);
  });
});

describe("parseInsert", () => {
  it("reads a success result", () => {
    const r = parseInsert(JSON.stringify({ summary: "넣었다", slide: 4, numbers: 15 }));
    expect(r).toEqual({ summary: "넣었다", slide: 4, numbers: 15 });
  });
  it("reports tool errors", () => {
    expect(parseInsert(JSON.stringify({ error: "실패", step: "range" })).error).toBe("실패");
  });
  it("reports unreadable output", () => {
    expect(parseInsert("not json").error).toBeTruthy();
    expect(parseInsert("{}").error).toBeTruthy();
    expect(parseInsert("null").error).toBeTruthy();
    expect(parseInsert("[]").error).toBeTruthy();
  });
});
