import type { RhwpEditor } from "@rhwp/editor";
import { describe, expect, it, vi } from "vitest";
import {
  beginSave,
  endSave,
  focusHwpParagraph,
  isCurrentHwpEditor,
  notifyHwpSaved,
  registerHwpEditor,
  unregisterHwpEditor,
} from "./hwp";

function fakeEditor(seq = { value: 0 }): RhwpEditor {
  return {
    notifySaved: vi.fn(async () => undefined),
    getDocumentState: vi.fn(async () => ({ changeSeq: seq.value })),
  } as unknown as RhwpEditor;
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
    expect(await notifyHwpSaved({ path: "x.hwp", editor: e1, changeSeq: 0 })).toBe(false);
    expect(e1.notifySaved).not.toHaveBeenCalled();
    expect(e2.notifySaved).not.toHaveBeenCalled();
    expect(await notifyHwpSaved({ path: "x.hwp", editor: e2, changeSeq: 0 })).toBe(true);
    expect(e2.notifySaved).toHaveBeenCalledWith("x.hwp");
    unregisterHwpEditor("x.hwp", e2);
  });

  it("keeps the dirty mark when the document was edited after export", async () => {
    const seq = { value: 3 };
    const e = fakeEditor(seq);
    registerHwpEditor("y.hwp", e);
    seq.value = 4; // 내보낸 뒤 사용자가 더 고침
    expect(await notifyHwpSaved({ path: "y.hwp", editor: e, changeSeq: 3 })).toBe(false);
    expect(await notifyHwpSaved({ path: "y.hwp", editor: e, changeSeq: null })).toBe(false);
    expect(e.notifySaved).not.toHaveBeenCalled();
    expect(await notifyHwpSaved({ path: "y.hwp", editor: e, changeSeq: 4 })).toBe(true);
    unregisterHwpEditor("y.hwp", e);
  });
});

describe("focusHwpParagraph", () => {
  it("moves the open editor to the source paragraph", async () => {
    const calls: unknown[] = [];
    const editor = {
      focusTarget: async (t: unknown) => {
        calls.push(t);
        return { focused: true, page: 1 };
      },
    } as unknown as RhwpEditor;
    registerHwpEditor("m.hwp", editor);
    try {
      expect(await focusHwpParagraph("m.hwp", { section: 0, paragraph: 2, length: 7 }, 0)).toBe(
        true,
      );
      expect(calls).toEqual([
        {
          kind: "body_paragraph",
          section: 0,
          paragraph: 2,
          charOffset: 0,
          length: 7,
        },
      ]);
    } finally {
      unregisterHwpEditor("m.hwp", editor);
    }
  });

  it("returns false when the editor cannot navigate or is not open", async () => {
    const editor = {
      focusTarget: async () => {
        throw new Error("capability missing");
      },
    } as unknown as RhwpEditor;
    registerHwpEditor("old.hwp", editor);
    try {
      expect(await focusHwpParagraph("old.hwp", { section: 0, paragraph: 0, length: 1 }, 0)).toBe(
        false,
      );
    } finally {
      unregisterHwpEditor("old.hwp", editor);
    }
    expect(await focusHwpParagraph("none.hwp", { section: 0, paragraph: 0, length: 1 }, 0)).toBe(
      false,
    );
  });
});
