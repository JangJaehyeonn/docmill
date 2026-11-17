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

/** 내보낸 바이트와, 내보내기에 쓴 편집기 인스턴스. */
export interface HwpExport {
  editor: RhwpEditor;
  bytes: Uint8Array;
  /** 내보낼 때의 편집 순번 (rhwp `getDocumentState().changeSeq`, 모든 편집마다 1씩 증가). 알 수 없으면 null. */
  changeSeq: number | null;
}

async function changeSeqOf(editor: RhwpEditor): Promise<number | null> {
  try {
    return (await editor.getDocumentState()).changeSeq;
  } catch {
    return null;
  }
}

/** 열린 편집기의 내용을 원래 형식(hwp 또는 hwpx)으로 내보낸다. */
export async function exportHwp(path: string): Promise<HwpExport> {
  const editor = open.get(path);
  if (!editor) throw new Error("편집기가 열려 있지 않습니다.");
  const changeSeq = await changeSeqOf(editor);
  const bytes = await editor.hwpctrl.exportBytes(/\.hwpx$/i.test(path) ? "hwpx" : "hwp");
  return { editor, bytes, changeSeq };
}

/**
 * 저장 완료를 알린다 (수정됨 표시와 자동복구 draft 를 지운다). 다음일 때만 알린다.
 * - 내보내기에 쓴 편집기가 아직 그 경로의 편집기다 (다시 열어 새 편집기가 생기지 않았다).
 * - 내보낸 뒤 편집이 없었다 (편집 순번이 그대로다). 저장 중에 더 고친 내용은 이번 저장에 없으므로
 *   수정됨 표시를 남겨 둔다. 편집 순번을 알 수 없으면 안전하게 알리지 않는다.
 */
export async function notifyHwpSaved(saved: {
  path: string;
  editor: RhwpEditor;
  changeSeq: number | null;
}): Promise<boolean> {
  const { path, editor, changeSeq } = saved;
  if (!isCurrentHwpEditor(path, editor) || changeSeq === null) return false;
  if ((await changeSeqOf(editor)) !== changeSeq) return false;
  await editor.notifySaved(path.split("/").pop());
  return true;
}

/** 이 편집기가 지금 그 경로에 등록된 편집기인지. */
export function isCurrentHwpEditor(path: string, editor: RhwpEditor): boolean {
  return open.get(path) === editor;
}

/** 경로별 저장 중 표시. 같은 문서의 저장이 겹쳐 오래된 내용이 최신을 덮지 않게 한다. */
const saving = new Set<string>();

/** 저장을 시작할 수 있으면 true 를 돌려주고 저장 중으로 표시한다. */
export function beginSave(path: string): boolean {
  if (saving.has(path)) return false;
  saving.add(path);
  return true;
}

/** 저장 중 표시를 지운다. */
export function endSave(path: string): void {
  saving.delete(path);
}

/** 열린 한글 편집기에 저장하지 않은 편집이 있는지. 상태를 알 수 없으면 안전하게 있다고 본다. */
export async function hasUnsavedHwpEdits(path: string): Promise<boolean> {
  const editor = open.get(path);
  if (!editor) return false;
  try {
    return (await editor.getDocumentState()).dirty;
  } catch {
    return true;
  }
}
