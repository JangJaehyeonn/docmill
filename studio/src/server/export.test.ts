import { describe, expect, it } from "vitest";
import { failedChecks, failureLine, looksLikePdf, parseKind } from "./export.js";

describe("export helpers", () => {
  it("accepts only known kinds", () => {
    expect(parseKind("pdf")).toBe("pdf");
    expect(parseKind("mail")).toBe("mail");
    expect(parseKind("send")).toBeNull();
    expect(parseKind(undefined)).toBeNull();
  });

  it("keeps only failed checks", () => {
    const checks = [
      { name: "개인정보", status: "실패", count: 2 },
      { name: "날짜 요일", status: "주의", count: 1 },
      { name: "서식", status: "통과", count: 0 },
    ];
    expect(failedChecks(checks).map(failureLine)).toEqual(["개인정보: 2곳"]);
  });

  it("recognises PDF bytes", () => {
    expect(looksLikePdf(Buffer.from("%PDF-1.7\n..."))).toBe(true);
    expect(looksLikePdf(Buffer.from("<html>"))).toBe(false);
  });
});
