import { describe, expect, it } from "vitest";
import { closeTab, openTab, type TabState } from "./tabs";

const a = { path: "a.xlsx", name: "a.xlsx" };
const b = { path: "b.docx", name: "b.docx" };
const empty: TabState = { tabs: [], active: null };

describe("tabs", () => {
  it("opens two files as two tabs and activates the latest", () => {
    const s = openTab(openTab(empty, a), b);
    expect(s.tabs).toHaveLength(2);
    expect(s.active).toBe("b.docx");
  });

  it("reopening an open file only activates it", () => {
    const s = openTab(openTab(openTab(empty, a), b), a);
    expect(s.tabs).toHaveLength(2);
    expect(s.active).toBe("a.xlsx");
  });

  it("closing the active tab activates a neighbour", () => {
    const s = closeTab(openTab(openTab(empty, a), b), "b.docx");
    expect(s.active).toBe("a.xlsx");
    expect(closeTab(s, "a.xlsx").active).toBeNull();
  });
});
