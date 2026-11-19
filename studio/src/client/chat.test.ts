import { describe, expect, it } from "vitest";
import {
  applyEvent,
  formatPreflight,
  isCompareTool,
  parseComparison,
  PREFLIGHT_FAILED,
  PREFLIGHT_NO_EDITOR_NOTE,
  PREFLIGHT_UNSAVED_NOTE,
  preflightText,
  splitEvents,
  type ChatItem,
} from "./chat";

describe("formatPreflight", () => {
  it("항목별 상태와 위치 개수를 줄마다 적는다", () => {
    const text = formatPreflight({
      checks: [
        { name: "자리표시자", status: "주의", count: 2 },
        { name: "개인정보", status: "통과", count: 0 },
      ],
    });
    expect(text).toBe("보내기 전 점검 결과\n자리표시자: 주의 (2곳)\n개인정보: 통과");
  });

  it("실패하면 이유를 보인다", () => {
    expect(formatPreflight({ checks: [], error: "파일 없음" })).toContain("파일 없음");
  });
});

describe("applyEvent", () => {
  it("도구 호출 카드에 결과를 붙이고 마지막 메시지를 뒤에 더한다", () => {
    let items: ChatItem[] = [{ kind: "user", id: 1, text: "안녕" }];
    items = applyEvent(items, { type: "tool", id: "t0", name: "verify_numbers", input: {} }, 2);
    items = applyEvent(items, { type: "tool_result", id: "t0", text: "ok", paths: [] }, 3);
    items = applyEvent(items, { type: "assistant", text: "끝" }, 4);
    expect(items.map((i) => i.kind)).toEqual(["user", "tool", "assistant"]);
    expect(items[1]).toMatchObject({ result: "ok" });
  });
});

describe("splitEvents", () => {
  it("덜 온 줄은 남겨 두고 다음 조각과 이어 읽는다", () => {
    const first = splitEvents('{"type":"done"}\n{"type":"ass');
    expect(first.events).toEqual([{ type: "done" }]);
    const second = splitEvents(first.rest + 'istant","text":"a"}\n');
    expect(second.events).toEqual([{ type: "assistant", text: "a" }]);
  });
});

describe("preflightText", () => {
  const body = { checks: [{ name: "개인정보", status: "통과", count: 0 }] };
  const okFetch = (async () => new Response(JSON.stringify(body))) as typeof fetch;

  it("저장이 끝나면 점검 결과만 보인다", async () => {
    const text = await preflightText("a.docx", async () => "saved" as const, okFetch);
    expect(text).toBe("보내기 전 점검 결과\n개인정보: 통과");
  });

  it("편집 세션이 없으면 디스크 파일로 점검하고 안내 한 줄을 붙인다", async () => {
    const calls: string[] = [];
    const fetchFn = (async (url: string) => {
      calls.push(url);
      return new Response(JSON.stringify(body));
    }) as typeof fetch;
    const text = await preflightText("a.docx", async () => "no-editor" as const, fetchFn);
    expect(calls).toEqual(["/api/preflight?path=a.docx"]);
    expect(text).toBe(`${PREFLIGHT_NO_EDITOR_NOTE}\n보내기 전 점검 결과\n개인정보: 통과`);
  });

  it("저장이 실패하거나 예외가 나도 점검하되, 편집 내용이 빠졌다는 경고를 붙인다", async () => {
    const expected = `${PREFLIGHT_UNSAVED_NOTE}\n보내기 전 점검 결과\n개인정보: 통과`;
    expect(await preflightText("a.docx", async () => "failed" as const, okFetch)).toBe(expected);
    expect(
      await preflightText("a.docx", async () => Promise.reject(new Error("x")), okFetch),
    ).toBe(expected);
  });

  it("점검 요청 자체가 실패하면 오류 문구를 쓴다", async () => {
    const failFetch = (async () => Promise.reject(new Error("net"))) as typeof fetch;
    expect(await preflightText("a.docx", async () => "saved" as const, failFetch)).toBe(PREFLIGHT_FAILED);
  });
});

describe("compare_versions 결과", () => {
  it("MCP 접두어가 붙은 이름도 비교 도구로 알아본다", () => {
    expect(isCompareTool("compare_versions")).toBe(true);
    expect(isCompareTool("mcp__docmill__compare_versions")).toBe(true);
    expect(isCompareTool("verify_numbers")).toBe(false);
  });

  it("changes 를 종류·위치·이전 값·새 값으로 해석한다", () => {
    const text = JSON.stringify({
      changes: [{ kind: "숫자", location: "S B1", old: "7", new: "9" }],
      summary: "바뀐 곳 1곳 (숫자 1곳)",
    });
    expect(parseComparison(text)).toEqual({
      changes: [{ kind: "숫자", location: "S B1", old: "7", new: "9" }],
      summary: "바뀐 곳 1곳 (숫자 1곳)",
      error: undefined,
    });
  });

  it("모양이 맞지 않으면 null 이다", () => {
    expect(parseComparison(undefined)).toBeNull();
    expect(parseComparison("not json")).toBeNull();
    expect(parseComparison('{"other":1}')).toBeNull();
  });
});
