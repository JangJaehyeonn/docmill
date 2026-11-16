import { describe, expect, it } from "vitest";
import { afterRefresh, readOpenParam } from "./startup";

describe("readOpenParam", () => {
  it("?open= 값을 읽고 없으면 null 을 돌려준다", () => {
    expect(readOpenParam("?open=a.xlsx")).toBe("a.xlsx");
    expect(readOpenParam("?open=")).toBeNull();
    expect(readOpenParam("")).toBeNull();
  });
});

describe("afterRefresh", () => {
  it("초기 조회가 실패하면 보류된 open 을 남겨 둔다", () => {
    expect(afterRefresh("a.xlsx", false)).toEqual({ open: null, pending: "a.xlsx" });
  });

  it("조회가 성공하면 보류된 open 을 한 번만 꺼낸다", () => {
    const first = afterRefresh("a.xlsx", true);
    expect(first).toEqual({ open: "a.xlsx", pending: null });
    // 이미 처리한 뒤의 재시도는 목록만 갱신한다 (다시 열지 않는다).
    expect(afterRefresh(first.pending, true)).toEqual({ open: null, pending: null });
  });

  it("실패 뒤 재시도가 성공하면 보류된 open 을 처리한다", () => {
    const failed = afterRefresh("a.xlsx", false);
    expect(afterRefresh(failed.pending, true)).toEqual({ open: "a.xlsx", pending: null });
  });
});
