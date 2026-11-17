import { describe, expect, it } from "vitest";
import { parsePreflight } from "./preflight.js";

describe("parsePreflight", () => {
  it("항목별 이름·상태·위치 개수를 꺼낸다", () => {
    const out = JSON.stringify({
      checks: [
        { name: "자리표시자", status: "주의", locations: [{}, {}] },
        { name: "개인정보", status: "통과", locations: [] },
        { bad: true },
      ],
    });
    expect(parsePreflight(out).checks).toEqual([
      { name: "자리표시자", status: "주의", count: 2 },
      { name: "개인정보", status: "통과", count: 0 },
    ]);
  });

  it("도구 오류와 깨진 출력은 error 로 돌려준다", () => {
    expect(parsePreflight('{"error":"파일 없음"}').error).toBe("파일 없음");
    expect(parsePreflight("nope").error).toBeTruthy();
    expect(parsePreflight("{}").error).toBeTruthy();
  });
});
