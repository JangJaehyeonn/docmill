import { describe, expect, it } from "vitest";
import { applyEvent, splitEvents, type ChatItem } from "./chat";

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
