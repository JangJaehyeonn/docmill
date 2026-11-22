import { describe, expect, it } from "vitest";
import { parseSentenceRefs, sentenceAction } from "./sentences";

describe("parseSentenceRefs", () => {
  it("keeps well-formed sentences only", () => {
    const body = {
      sentences: [
        { text: "a", source: "s.xlsx", label: "C2:C4" },
        { text: "b", source: null, label: "A1" },
        { text: 1, source: "s.xlsx", label: "x" },
        { text: "c", label: "x" },
      ],
    };
    expect(parseSentenceRefs(body)).toEqual([
      { text: "a", source: "s.xlsx", label: "C2:C4" },
      { text: "b", source: null, label: "A1" },
    ]);
    expect(parseSentenceRefs(null)).toEqual([]);
    expect(parseSentenceRefs({ sentences: "x" })).toEqual([]);
  });
});

describe("sentenceAction", () => {
  const ref = { text: "매출 합계: 10", source: "sales.xlsx", label: "매출!C2:C16" };

  it("activates an already open source tab", () => {
    expect(sentenceAction(ref, ["sales.xlsx", "report.pptx"])).toEqual({
      kind: "highlight",
      highlight: { path: "sales.xlsx", label: "매출!C2:C16", text: "매출 합계: 10" },
      open: false,
    });
  });

  it("opens the source when it is not open yet", () => {
    const action = sentenceAction(ref, ["report.pptx"]);
    expect(action.kind === "highlight" && action.open).toBe(true);
  });

  it("explains when the source is outside the workspace", () => {
    const action = sentenceAction({ ...ref, source: null }, []);
    expect(action.kind).toBe("missing");
    expect(action.kind === "missing" && action.message).toContain("매출!C2:C16");
  });
});
