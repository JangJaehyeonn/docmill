import { promises as fs } from "node:fs";
import { resolveSource } from "./links.js";
import { resolveInWorkspace } from "./workspace.js";

/** 한글 편집기에서 근거 문단으로 이동할 위치 (구역·문단 0부터, 문단 글자 수). */
export interface HwpTarget {
  section: number;
  paragraph: number;
  length: number;
}

/** 출처 파일에 적힌 문장 하나 (FR-47): 문장, 원본 파일(출처 파일에 적힌 그대로), 근거 위치 문구. */
export interface SentenceEntry {
  text: string;
  file: string;
  label: string;
  target: HwpTarget | null;
}

/** 화면에 보낼 문장 출처: 원본이 작업 폴더 안이면 작업 폴더 기준 경로, 아니면 null. */
export interface SentenceRef {
  text: string;
  source: string | null;
  label: string;
  /** 한글 원본이면 편집기에서 바로 이동할 본문 문단. 없으면 null. */
  target: HwpTarget | null;
}

const isIndex = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v) && v >= 0;

/** 출처의 `hwp` 항목에서 편집기 이동 위치를 꺼낸다. 형식이 맞지 않으면 null. */
export function hwpTarget(source: Record<string, unknown>): HwpTarget | null {
  const hwp = source.hwp as Record<string, unknown> | undefined;
  if (typeof hwp !== "object" || hwp === null) return null;
  const { section, paragraph, length } = hwp;
  if (!isIndex(section) || !isIndex(paragraph) || !isIndex(length) || length > 4000) return null;
  return { section, paragraph, length };
}

/** 근거 위치를 사람이 읽는 문구로 만든다. 셀 범위는 `시트!범위`, 문단은 `N번째 문단`(1부터). */
export function sourceLabel(source: Record<string, unknown>): string {
  const range = source.range;
  if (typeof range === "string" && range !== "") {
    const sheet = source.sheet;
    return typeof sheet === "string" && sheet !== "" ? `${sheet}!${range}` : range;
  }
  const paragraph = source.paragraph;
  if (typeof paragraph === "number" && Number.isInteger(paragraph) && paragraph >= 0) {
    return `${paragraph + 1}번째 문단`;
  }
  return "위치 정보 없음";
}

/** 출처 파일 내용에서 `sentences` 항목을 문서 순서대로 꺼낸다. 형식이 맞지 않는 항목은 뺀다. */
export function parseSentences(provenance: string): SentenceEntry[] {
  let data: { sentences?: unknown };
  try {
    data = JSON.parse(provenance) as typeof data;
  } catch {
    return [];
  }
  if (!Array.isArray(data.sentences)) return [];
  const out: SentenceEntry[] = [];
  for (const item of data.sentences as { text?: unknown; source?: unknown }[]) {
    const text = typeof item?.text === "string" ? item.text.trim() : "";
    const source = item?.source;
    if (text === "" || typeof source !== "object" || source === null) continue;
    const file = (source as { file?: unknown }).file;
    if (typeof file !== "string" || file === "") continue;
    const fields = source as Record<string, unknown>;
    out.push({
      text,
      file,
      label: sourceLabel(fields),
      target: hwpTarget(fields),
    });
  }
  return out;
}

/**
 * 문서 옆 출처 파일의 문장 출처를 읽어, 원본 경로를 작업 폴더 기준으로 바꿔 돌려준다.
 * 원본이 작업 폴더 밖이거나 없으면 source 는 null 이다. 출처 파일이 없으면 빈 목록.
 */
export async function sentenceRefs(root: string, docAbs: string): Promise<SentenceRef[]> {
  let text: string;
  try {
    text = await fs.readFile(`${docAbs}.provenance.json`, "utf-8");
  } catch {
    return [];
  }
  const out: SentenceRef[] = [];
  const resolved = new Map<string, string | null>();
  for (const entry of parseSentences(text)) {
    if (!resolved.has(entry.file))
      resolved.set(entry.file, await workspacePath(root, docAbs, entry.file));
    out.push({
      text: entry.text,
      source: resolved.get(entry.file) ?? null,
      label: entry.label,
      target: entry.target,
    });
  }
  return out;
}

async function workspacePath(root: string, docAbs: string, file: string): Promise<string | null> {
  try {
    const abs = await resolveSource(root, docAbs, file);
    if (abs === null) return null;
    const rel = await resolveInWorkspace(root, abs);
    return (await fs.stat(abs)).isFile() ? rel : null;
  } catch {
    return null;
  }
}
