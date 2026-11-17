import type { RhwpEditor } from "@rhwp/editor";

/** 한글(hwp·hwpx) 파일인지. */
export function isHwpPath(path: string): boolean {
  return /\.(hwp|hwpx)$/i.test(path);
}

/** 열려 있는 한글 편집기 (경로별). 저장 버튼이 여기서 편집기를 찾는다. */
const open = new Map<string, RhwpEditor>();

export function registerHwpEditor(path: string, editor: RhwpEditor): void {
  open.set(path, editor);
}

export function unregisterHwpEditor(path: string, editor: RhwpEditor): void {
  if (open.get(path) === editor) open.delete(path);
}

/** 페이지에 노출하는 편집기 제어 창구 (자동화·테스트용). */
export interface RhwpHandle {
  insertText(text: string): Promise<void>;
  getText(): Promise<string>;
}

/** 편집기 인스턴스를 글자 넣기·읽기 두 동작으로 감싼다. 문서 맨 앞에 넣는다. */
export function makeHandle(editor: RhwpEditor): RhwpHandle {
  return {
    async insertText(text: string): Promise<void> {
      await editor.hwpctrl.call("SetTextFile", [text, "UNICODE", ""]);
    },
    async getText(): Promise<string> {
      return String((await editor.hwpctrl.call("GetTextFile", ["UNICODE", ""])) ?? "");
    },
  };
}

/** 열린 편집기의 내용을 원래 형식(hwp 또는 hwpx)으로 내보낸다. */
export async function exportHwp(path: string): Promise<Uint8Array> {
  const editor = open.get(path);
  if (!editor) throw new Error("편집기가 열려 있지 않습니다.");
  return editor.hwpctrl.exportBytes(/\.hwpx$/i.test(path) ? "hwpx" : "hwp");
}

/** 열린 편집기에 저장 완료를 알린다 (수정됨 표시를 지운다). */
export async function notifyHwpSaved(path: string): Promise<void> {
  await open.get(path)?.notifySaved(path.split("/").pop());
}
