import { describe, expect, it } from "vitest";
import { createLatestGate } from "./similar";

describe("createLatestGate", () => {
  it("늦게 도착한 이전 검색 응답은 무시한다", async () => {
    const gate = createLatestGate();
    const shown: string[][] = [];
    let releaseFirst: (v: string[]) => void = () => {};
    const run = (p: Promise<string[]>) => {
      const seq = gate.begin();
      return p.then((r) => {
        if (gate.isLatest(seq)) shown.push(r);
      });
    };
    const first = run(new Promise((res) => (releaseFirst = res)));
    const second = run(Promise.resolve(["new.docx"]));
    await second;
    releaseFirst(["old.docx"]);
    await first;
    expect(shown).toEqual([["new.docx"]]);
  });

  it("순서대로 끝나는 검색은 그대로 반영한다", () => {
    const gate = createLatestGate();
    const a = gate.begin();
    expect(gate.isLatest(a)).toBe(true);
    const b = gate.begin();
    expect(gate.isLatest(a)).toBe(false);
    expect(gate.isLatest(b)).toBe(true);
  });
});
