import { describe, expect, it } from "vitest";
import { baseName, matchByName, resolvePicked } from "./pick";

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

describe("resolvePicked", () => {
  const files = ["inside/report.xlsx", "a/plan.docx", "b/plan.docx"];

  it("실제 경로가 있으면 이름이 같은 작업 폴더 파일 대신 그 경로를 연다", () => {
    expect(resolvePicked(files, "report.xlsx", "/outside/report.xlsx")).toEqual({
      kind: "open",
      path: "/outside/report.xlsx",
    });
  });

  it("실제 경로가 없으면 이름이 하나만 일치해도 자동으로 열지 않고 고르게 한다", () => {
    expect(resolvePicked(files, "report.xlsx", null)).toEqual({
      kind: "choose",
      paths: ["inside/report.xlsx"],
    });
  });

  it("실제 경로가 없고 여러 개가 일치하면 모두 선택지로 보여 준다", () => {
    expect(resolvePicked(files, "plan.docx", null)).toEqual({
      kind: "choose",
      paths: ["a/plan.docx", "b/plan.docx"],
    });
  });

  it("빈 실제 경로는 경로가 없는 것으로 본다", () => {
    expect(resolvePicked(files, "missing.pptx", "")).toEqual({ kind: "missing" });
  });
});
