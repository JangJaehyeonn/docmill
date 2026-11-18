import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import { resolveInWorkspace } from "./workspace.js";

/** 문서와 연결된 원본 파일 하나: 작업 폴더 기준 경로, 저장 판(수정 시각·크기), 문서보다 새로운지. */
export interface LinkSource {
  path: string;
  version: string;
  newer: boolean;
}

/** 바뀐 위치 하나 (refresh_links 결과의 changes 항목). */
export interface LinkChange {
  label: string;
  old: number;
  new: number;
}

/** 새로 고침 결과: 요약·바뀐 위치·주의 문구 또는 실패 이유. */
export interface RefreshResult {
  summary: string;
  changes: LinkChange[];
  warnings: string[];
  error?: string;
}

/** 문서 옆 출처 파일(`<문서>.provenance.json`) 내용에서 원본 파일 경로를 처음 나온 순서로 꺼낸다. */
export function linkedFiles(provenance: string): string[] {
  let data: { numbers?: unknown };
  try {
    data = JSON.parse(provenance) as typeof data;
  } catch {
    return [];
  }
  if (!Array.isArray(data.numbers)) return [];
  const files: string[] = [];
  for (const n of data.numbers as { source?: { file?: unknown } }[]) {
    const file = n?.source?.file;
    if (typeof file === "string" && file !== "" && !files.includes(file)) files.push(file);
  }
  return files;
}

/**
 * 문서와 연결된 원본 중 작업 폴더 안에 있는 파일의 상태를 돌려준다.
 * 원본이 출처 파일보다 나중에 저장됐으면 newer 가 true 다. 출처 파일이 없으면 빈 목록.
 */
/** `docmill refresh-links` 와 같은 규칙으로 원본 경로를 푼다: 절대 경로는 그대로, 상대 경로는 작업 폴더 기준(없으면 문서 폴더 기준). */
async function resolveSource(root: string, docAbs: string, file: string): Promise<string> {
  const abs = path.resolve(root, file);
  if (path.isAbsolute(file)) return abs;
  try {
    if ((await fs.stat(abs)).isFile()) return abs;
  } catch {
    // 작업 폴더 기준에 없으면 문서 폴더 기준으로 본다.
  }
  return path.resolve(path.dirname(docAbs), file);
}

/** 문서의 출처 파일이 가리키는 원본이 모두 작업 폴더 안의 실제 파일이면 true (출처 파일이 없으면 true). */
export async function sourcesInside(root: string, docAbs: string): Promise<boolean> {
  let text: string;
  try {
    text = await fs.readFile(`${docAbs}.provenance.json`, "utf-8");
  } catch {
    return true;
  }
  for (const file of linkedFiles(text)) {
    try {
      await resolveInWorkspace(root, await resolveSource(root, docAbs, file));
    } catch {
      return false;
    }
  }
  return true;
}

export async function linkStatus(root: string, docAbs: string): Promise<LinkSource[]> {
  const provPath = `${docAbs}.provenance.json`;
  let text: string;
  let provTime: number;
  try {
    text = await fs.readFile(provPath, "utf-8");
    provTime = (await fs.stat(provPath)).mtimeMs;
  } catch {
    return [];
  }
  const out: LinkSource[] = [];
  for (const file of linkedFiles(text)) {
    try {
      const rel = await resolveInWorkspace(root, await resolveSource(root, docAbs, file));
      const info = await fs.stat(path.join(root, ...rel.split("/")));
      out.push({ path: rel, version: `${info.mtimeMs}:${info.size}`, newer: info.mtimeMs > provTime });
    } catch {
      // 작업 폴더 밖이거나 없는 원본은 알리지 않는다.
    }
  }
  return out;
}

/** `docmill refresh-links` 가 출력한 JSON 을 결과로 바꾼다. 형식이 맞지 않으면 error. */
export function parseRefresh(stdout: string): RefreshResult {
  const failed = (error: string): RefreshResult => ({ summary: "", changes: [], warnings: [], error });
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(stdout) as Record<string, unknown>;
  } catch {
    return failed("새로 고침 결과를 읽지 못했어요.");
  }
  if (typeof data.error === "string") return failed(data.error);
  if (!Array.isArray(data.changes)) return failed("새로 고침 결과를 읽지 못했어요.");
  const changes: LinkChange[] = [];
  for (const c of data.changes as Record<string, unknown>[]) {
    if (typeof c?.label === "string" && typeof c.old === "number" && typeof c.new === "number") {
      changes.push({ label: c.label, old: c.old, new: c.new });
    }
  }
  const warnings = Array.isArray(data.warnings)
    ? data.warnings.filter((w): w is string => typeof w === "string")
    : [];
  const summary = typeof data.summary === "string" ? data.summary : "";
  return { summary, changes, warnings };
}

/** docmill 도구 `refresh_links` 를 CLI(`docmill refresh-links`)로 불러 문서 숫자를 고친다. */
export function runRefresh(abs: string, cwd: string, timeoutMs = 60_000): Promise<RefreshResult> {
  return new Promise((resolve) => {
    execFile(
      "docmill",
      ["refresh-links", abs],
      { cwd, timeout: timeoutMs, encoding: "utf-8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (err, stdout) =>
        resolve(
          err
            ? { summary: "", changes: [], warnings: [], error: "새 숫자로 고치지 못했어요." }
            : parseRefresh(stdout),
        ),
    );
  });
}
