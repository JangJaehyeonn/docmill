import { describe, expect, it } from "vitest";
import { parseSimilar } from "./similar.js";

describe("parseSimilar", () => {
  it("목록을 꺼낸다", () => {
    const json = JSON.stringify({ similar: [{ path: "a.docx", title: "주간 보고", score: 1 }, { bad: 1 }] });
    expect(parseSimilar(json)).toEqual([{ path: "a.docx", title: "주간 보고" }]);
  });
  it("깨진 출력은 빈 목록", () => {
    expect(parseSimilar("oops")).toEqual([]);
    expect(parseSimilar("{}")).toEqual([]);
  });
});
