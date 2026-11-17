import { promises as fs } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { replaceFile } from "./onlyoffice.js";
import { resolveInWorkspace } from "./workspace.js";

/** 도구 결과에서 읽은 미리보기 정보 (경로는 아직 확인 전). */
export interface PreviewInfo {
  path: string;
  copy: string;
  summary: string[];
}

/** 서버가 기억하는 대기 중인 미리보기: 원본·사본의 작업 폴더 기준 상대 경로. */
export interface PendingPreview {
  rel: string;
  copy: string;
}

const LABELS: Record<string, string> = {
  changed_slides: "바뀐 슬라이드",
  changed_cells: "바뀐 셀",
  changed_paragraphs: "바뀐 문단",
};

/** 도구 결과 텍스트(JSON)에서 `modified_copy` 가 있으면 미리보기 정보를 만든다. 없으면 null. */
export function parsePreview(text: string): PreviewInfo | null {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  if (data === null || typeof data !== "object" || Array.isArray(data)) return null;
  const obj = data as Record<string, unknown>;
  if (typeof obj.modified_copy !== "string" || typeof obj.path !== "string") return null;
  const summary: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    if (k === "path" || k === "modified_copy") continue;
    const label = LABELS[k] ?? k;
    if (typeof v === "number") summary.push(`${label}: ${v}개`);
    else if (typeof v === "string" || typeof v === "boolean") summary.push(`${label}: ${String(v)}`);
    else if (v !== null && typeof v === "object") summary.push(`${label}: ${JSON.stringify(v)}`);
  }
  return { path: obj.path, copy: obj.modified_copy, summary };
}

/** 원본·사본이 모두 작업 폴더 안 문서이고 서로 다른 파일이면 대기 목록에 넣고 ID 를 돌려준다. */
export async function registerPreview(
  store: Map<string, PendingPreview>,
  root: string,
  info: PreviewInfo,
): Promise<{ id: string; rel: string } | null> {
  try {
    const rel = await resolveInWorkspace(root, info.path);
    const copy = await resolveInWorkspace(root, info.copy);
    if (rel === copy || path.extname(rel).toLowerCase() !== path.extname(copy).toLowerCase()) {
      return null;
    }
    const id = randomUUID();
    store.set(id, { rel, copy });
    return { id, rel };
  } catch {
    return null;
  }
}

/** 사본을 지우고, 비어 있는 사본 폴더(작업 폴더 아래의 숨김 폴더)도 정리한다. */
async function removeCopy(root: string, copyRel: string): Promise<void> {
  const abs = path.join(root, ...copyRel.split("/"));
  await fs.rm(abs, { force: true });
  const dir = path.dirname(abs);
  if (dir !== root && path.basename(dir).startsWith(".")) {
    await fs.rmdir(dir).catch(() => undefined);
  }
}

/** 적용: 원본을 사본 내용으로 바꾸고 사본을 지운다. 알 수 없는 ID 면 null. */
export async function applyPreview(
  store: Map<string, PendingPreview>,
  root: string,
  id: string,
): Promise<string | null> {
  const p = store.get(id);
  if (!p) return null;
  const data = await fs.readFile(path.join(root, ...p.copy.split("/")));
  await replaceFile(path.join(root, ...p.rel.split("/")), data);
  store.delete(id);
  await removeCopy(root, p.copy);
  return p.rel;
}

/** 취소: 사본만 지운다. 알 수 없는 ID 면 false. */
export async function cancelPreview(
  store: Map<string, PendingPreview>,
  root: string,
  id: string,
): Promise<boolean> {
  const p = store.get(id);
  if (!p) return false;
  store.delete(id);
  await removeCopy(root, p.copy);
  return true;
}
