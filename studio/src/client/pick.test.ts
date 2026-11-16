import { describe, expect, it } from "vitest";
import { baseName, matchByName } from "./pick";

describe("matchByName", () => {
  const files = ["a/report.xlsx", "b/report.xlsx", "plan.docx", "sub/plan.docx.bak"];

  it("이름이 같은 파일을 모두 돌려준다", () => {
    expect(matchByName(files, "report.xlsx")).toEqual(["a/report.xlsx", "b/report.xlsx"]);
  });

  it("파일 이름 전체가 같을 때만 고른다", () => {
    expect(matchByName(files, "plan.docx")).toEqual(["plan.docx"]);
    expect(matchByName(files, "missing.pptx")).toEqual([]);
  });

  it("baseName 은 마지막 경로 부분을 돌려준다", () => {
    expect(baseName("a/b/c.hwp")).toBe("c.hwp");
    expect(baseName("c.hwp")).toBe("c.hwp");
  });
});
