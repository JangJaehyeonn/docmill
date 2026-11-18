import { describe, expect, it } from "vitest";
import { clearOfficePending, setOfficePending, waitOfficeSynced } from "./officeSync";

describe("officeSync", () => {
  it("resolves immediately when nothing is pending", async () => {
    expect(await waitOfficeSynced("none.xlsx", 50)).toBe(true);
    setOfficePending("a.xlsx", false);
    expect(await waitOfficeSynced("a.xlsx", 50)).toBe(true);
  });

  it("waits until the editor reports the changes were sent", async () => {
    setOfficePending("b.xlsx", true);
    const waiting = waitOfficeSynced("b.xlsx", 1000);
    setTimeout(() => setOfficePending("b.xlsx", false), 20);
    expect(await waiting).toBe(true);
  });

  it("gives up after the timeout but wakes on close", async () => {
    setOfficePending("c.xlsx", true);
    expect(await waitOfficeSynced("c.xlsx", 30)).toBe(false);
    const waiting = waitOfficeSynced("c.xlsx", 1000);
    clearOfficePending("c.xlsx");
    expect(await waiting).toBe(false);
  });
});
