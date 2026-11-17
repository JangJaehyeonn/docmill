import type { RhwpEditor } from "@rhwp/editor";
import { describe, expect, it, vi } from "vitest";
import {
  beginSave,
  endSave,
  isCurrentHwpEditor,
  notifyHwpSaved,
  registerHwpEditor,
  unregisterHwpEditor,
} from "./hwp";

function fakeEditor(): RhwpEditor {
  return { notifySaved: vi.fn(async () => undefined) } as unknown as RhwpEditor;
}

describe("hwp save helpers", () => {
  it("allows only one save per path at a time", () => {
    expect(beginSave("a.hwp")).toBe(true);
    expect(beginSave("a.hwp")).toBe(false);
    expect(beginSave("b.hwp")).toBe(true);
    endSave("a.hwp");
    expect(beginSave("a.hwp")).toBe(true);
    endSave("a.hwp");
    endSave("b.hwp");
  });

  it("notifies only the editor that was exported", async () => {
    const e1 = fakeEditor();
    const e2 = fakeEditor();
    registerHwpEditor("x.hwp", e1);
    // 저장 중에 같은 파일을 다시 열어 새 편집기가 등록된 상황.
    unregisterHwpEditor("x.hwp", e1);
    registerHwpEditor("x.hwp", e2);
    expect(isCurrentHwpEditor("x.hwp", e1)).toBe(false);
    expect(await notifyHwpSaved("x.hwp", e1)).toBe(false);
    expect(e1.notifySaved).not.toHaveBeenCalled();
    expect(e2.notifySaved).not.toHaveBeenCalled();
    expect(await notifyHwpSaved("x.hwp", e2)).toBe(true);
    expect(e2.notifySaved).toHaveBeenCalledWith("x.hwp");
    unregisterHwpEditor("x.hwp", e2);
  });
});
