import { describe, expect, it } from "vitest";
import { parseSuggestions } from "./suggest.js";

describe("parseSuggestions", () => {
  it("keeps valid items and caps at three", () => {
    const item = (n: number) => ({ label: `l${n}`, tool: "t", message: `m${n}` });
    const out = parseSuggestions(JSON.stringify({ suggestions: [1, 2, 3, 4].map(item) }));
    expect(out.map((s) => s.label)).toEqual(["l1", "l2", "l3"]);
  });
  it("drops malformed items", () => {
    expect(parseSuggestions(JSON.stringify({ suggestions: [{ label: "x" }] }))).toEqual([]);
  });
  it("returns empty for non-JSON or errors", () => {
    expect(parseSuggestions("oops")).toEqual([]);
    expect(parseSuggestions(JSON.stringify({ error: "x" }))).toEqual([]);
  });
});
