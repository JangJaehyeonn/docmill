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

describe("officeSync settle (#162)", () => {
  it("waits for a late 'unsent changes' report that arrives just after save was pressed", async () => {
    setOfficePending("late.xlsx", false);
    let sent = false;
    // Enter 직후 저장: "보내지 않은 변경 있음"이 50ms 늦게 오고, 150ms 뒤에 다 보냈다고 알린다.
    setTimeout(() => setOfficePending("late.xlsx", true), 50);
    setTimeout(() => {
      sent = true;
      setOfficePending("late.xlsx", false);
    }, 150);
    expect(await waitOfficeSynced("late.xlsx", 2000, 400)).toBe(true);
    expect(sent).toBe(true);
  });

  it("returns true after the settle window when no new edit appears", async () => {
    const start = Date.now();
    expect(await waitOfficeSynced("quiet.xlsx", 2000, 100)).toBe(true);
    expect(Date.now() - start).toBeGreaterThanOrEqual(90);
  });

  it("keeps waiting when another edit follows right after the changes were sent", async () => {
    setOfficePending("burst.xlsx", true);
    let done = false;
    setTimeout(() => setOfficePending("burst.xlsx", false), 20);
    setTimeout(() => setOfficePending("burst.xlsx", true), 40);
    setTimeout(() => {
      done = true;
      setOfficePending("burst.xlsx", false);
    }, 120);
    expect(await waitOfficeSynced("burst.xlsx", 2000, 400)).toBe(true);
    expect(done).toBe(true);
  });

  it("reports false when unsent changes remain at the deadline", async () => {
    setOfficePending("stuck.xlsx", false);
    setTimeout(() => setOfficePending("stuck.xlsx", true), 10);
    expect(await waitOfficeSynced("stuck.xlsx", 150, 400)).toBe(false);
  });
});
