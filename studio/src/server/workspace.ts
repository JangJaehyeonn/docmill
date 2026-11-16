import { promises as fs } from "node:fs";
import path from "node:path";

/** 열 수 있는 문서 확장자. */
export const DOC_EXTENSIONS = [".xlsx", ".pptx", ".docx", ".hwp", ".hwpx"];

export class WorkspaceError extends Error {}

function isInside(root: string, target: string): boolean {
  const rel = path.relative(root, target);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

/**
 * 요청 경로를 작업 폴더 기준 상대 경로(/ 구분)로 바꾼다.
 * 작업 폴더 밖이거나 열 수 없는 파일이면 한국어 사유와 함께 WorkspaceError 를 던진다.
 */
export async function resolveInWorkspace(workspace: string, requested: string): Promise<string> {
  const root = await fs.realpath(workspace);
  const candidate = path.resolve(root, requested);
  if (!isInside(root, candidate)) {
    throw new WorkspaceError("작업 폴더 밖의 경로는 열 수 없습니다.");
  }
  let real: string;
  try {
    real = await fs.realpath(candidate);
  } catch {
    throw new WorkspaceError("작업 폴더에 그런 파일이 없습니다.");
  }
  if (!isInside(root, real)) {
    throw new WorkspaceError("작업 폴더 밖의 경로는 열 수 없습니다.");
  }
  if (!(await fs.stat(real)).isFile()) {
    throw new WorkspaceError("파일이 아니라 폴더입니다. 작업 폴더 안의 문서 파일을 고르세요.");
  }
  if (!DOC_EXTENSIONS.includes(path.extname(real).toLowerCase())) {
    throw new WorkspaceError("지원하지 않는 파일 형식입니다 (xlsx, pptx, docx, hwp, hwpx).");
  }
  return path.relative(root, real).split(path.sep).join("/");
}

/** 내려가기 직전에 다시 확인한다: 심볼릭 링크가 아니고 실제 경로가 작업 폴더 안인 디렉터리만 true. */
async function isSafeDir(root: string, dir: string): Promise<boolean> {
  try {
    const info = await fs.lstat(dir);
    if (info.isSymbolicLink() || !info.isDirectory()) return false;
    return isInside(root, await fs.realpath(dir));
  } catch {
    return false;
  }
}

/** 작업 폴더 안의 문서 파일을 이름순(코드 포인트 순)으로 나열한다. */
export async function listFiles(workspace: string): Promise<string[]> {
  const root = await fs.realpath(workspace);
  const found: string[] = [];
  async function walk(dir: string): Promise<void> {
    for (const entry of await fs.readdir(dir, { withFileTypes: true })) {
      if (entry.name.startsWith(".") || entry.name === "node_modules") continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (await isSafeDir(root, full)) await walk(full);
      }
      else if (entry.isFile() && DOC_EXTENSIONS.includes(path.extname(entry.name).toLowerCase())) {
        found.push(path.relative(root, full).split(path.sep).join("/"));
      }
    }
  }
  await walk(root);
  return found.sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
}
