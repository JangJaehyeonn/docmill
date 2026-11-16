import { createHash, randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import path from "node:path";

export interface OnlyofficeOptions {
  /** 문서 서버 주소 (없으면 연결 안내만 보여준다). */
  url: string;
  /** 문서 서버가 studio 서버에 접근할 때 쓰는 호스트. */
  publicHost: string;
}

const DOC_TYPES: Record<string, "cell" | "slide" | "word"> = {
  xlsx: "cell",
  pptx: "slide",
  docx: "word",
};

/** ONLYOFFICE 편집기로 여는 확장자인지. */
export function isOnlyofficeFile(rel: string): boolean {
  return path.extname(rel).slice(1).toLowerCase() in DOC_TYPES;
}

/** 주소 끝의 슬래시를 없앤다. 빈 값이면 null. */
export function normalizeUrl(raw: string | undefined): string | null {
  const value = (raw ?? "").trim().replace(/\/+$/, "");
  return value === "" ? null : value;
}

/** 파일 상태에서 문서 키를 만든다 (파일이 바뀌면 키도 바뀐다). 영숫자만 쓴다. */
export function documentKey(rel: string, mtimeMs: number, size: number): string {
  return createHash("sha1").update(`${rel}\n${mtimeMs}\n${size}`).digest("hex");
}

export interface EditorConfig {
  documentType: "cell" | "slide" | "word";
  document: { fileType: string; key: string; title: string; url: string };
  editorConfig: { callbackUrl: string; lang: string; mode: "edit" };
}

/** 편집기에 넘길 설정을 만든다. `origin` 은 문서 서버가 접근할 studio 서버 주소. */
export function buildEditorConfig(rel: string, key: string, origin: string): EditorConfig {
  const ext = path.extname(rel).slice(1).toLowerCase();
  const documentType = DOC_TYPES[ext];
  if (!documentType) throw new Error("ONLYOFFICE 로 열 수 없는 형식입니다.");
  const q = `path=${encodeURIComponent(rel)}`;
  return {
    documentType,
    document: {
      fileType: ext,
      key,
      title: path.posix.basename(rel),
      url: `${origin}/onlyoffice/file?${q}`,
    },
    editorConfig: { callbackUrl: `${origin}/onlyoffice/callback?${q}`, lang: "ko", mode: "edit" },
  };
}

/** 저장 콜백 상태 중 파일을 받아야 하는 것: 2 = 저장 준비, 6 = 강제 저장. */
export function shouldSave(status: unknown): boolean {
  return status === 2 || status === 6;
}

/**
 * 문서 서버가 알려준 내려받기 주소를 설정된 문서 서버 주소 기준으로 바꾼다.
 * (문서 서버가 내부 호스트 이름을 돌려주는 경우를 대비한다.)
 */
export function rebaseDownloadUrl(downloadUrl: string, serverUrl: string): string {
  const src = new URL(downloadUrl);
  const base = new URL(serverUrl);
  if (src.host === base.host) return downloadUrl;
  return `${base.origin}${src.pathname}${src.search}`;
}

/** 내려받은 내용을 임시 파일에 쓴 뒤 이름을 바꿔 원본을 갱신한다. */
export async function replaceFile(target: string, data: Buffer): Promise<void> {
  const tmp = path.join(
    path.dirname(target),
    `.${path.basename(target)}.${randomUUID()}.docmill-tmp`,
  );
  try {
    await fs.writeFile(tmp, data);
    await fs.rename(tmp, target);
  } catch (err) {
    await fs.rm(tmp, { force: true });
    throw err;
  }
}
