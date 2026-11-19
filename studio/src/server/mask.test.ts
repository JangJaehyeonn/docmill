import { describe, expect, it } from "vitest";
import { registeredNames, unmaskText } from "./mask";
import { buildPrompt } from "./chat";

describe("mask", () => {
  it("자리표를 원래 값으로 되돌리고 모르는 자리표는 둔다", () => {
    expect(unmaskText("[이름1] [전화번호2]", { "[이름1]": "홍길동" })).toBe("홍길동 [전화번호2]");
  });

  it("등록한 이름을 쉼표로 읽는다", () => {
    expect(registeredNames({ DOCMILL_STUDIO_MASK_NAMES: "홍길동, 김영희,," })).toEqual(["홍길동", "김영희"]);
    expect(registeredNames({})).toEqual([]);
  });

  it("프롬프트에 가려진 문서 내용을 담는다", () => {
    const p = buildPrompt("/ws", {
      message: "요약",
      tabs: ["a.docx"],
      active: "a.docx",
      documents: [{ path: "a.docx", text: "[주민번호1]" }],
    });
    expect(p).toContain("[주민번호1]");
  });
});
