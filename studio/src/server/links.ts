import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
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
 * 출처 항목에 적힌 "읽은 원본의 판"(`source.read_version`, `sha256:<hex>`)을 원본 경로별로 모은다.
 * 범위 넣기(FR-44)와 새로 고침이 실제로 읽은 바이트의 판이라, 읽은 뒤 원본이 바뀌었는지 알 수 있다.
 */
export function readVersions(provenance: string): Map<string, Set<string>> {
  const found = new Map<string, Set<string>>();
  let data: { numbers?: unknown };
  try {
    data = JSON.parse(provenance) as typeof data;
  } catch {
    return found;
  }
  if (!Array.isArray(data.numbers)) return found;
  for (const n of data.numbers as { source?: { file?: unknown; read_version?: unknown } }[]) {
    const file = n?.source?.file;
    const version = n?.source?.read_version;
    if (typeof file !== "string" || typeof version !== "string") continue;
    const set = found.get(file) ?? new Set<string>();
    set.add(version);
    found.set(file, set);
  }
  return found;
}

/**
 * `docmill refresh-links` 와 같은 규칙으로 원본 경로를 푼다: 절대 경로는 그대로, 상대 경로는 작업 폴더 기준과
 * 문서 폴더 기준 중 있는 파일. 두 기준에 서로 다른 파일이 모두 있으면 어느 쪽인지 정할 수 없으므로 null.
 */
async function resolveSource(root: string, docAbs: string, file: string): Promise<string | null> {
  if (path.isAbsolute(file)) return path.resolve(file);
  const found: string[] = [];
  for (const candidate of [path.resolve(root, file), path.resolve(path.dirname(docAbs), file)]) {
    try {
      if (!(await fs.stat(candidate)).isFile()) continue;
      const real = await fs.realpath(candidate);
      if (!found.includes(real)) found.push(real);
    } catch {
      // 없는 후보는 건너뛴다.
    }
  }
  if (found.length > 1) return null;
  return found[0] ?? path.resolve(path.dirname(docAbs), file);
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
      const source = await resolveSource(root, docAbs, file);
      if (source === null) return false;
      await resolveInWorkspace(root, source);
    } catch {
      return false;
    }
  }
  return true;
}

/**
 * 문서와 연결된 원본 중 작업 폴더 안에 있는 파일의 상태를 돌려준다.
 * 원본이 출처 파일보다 나중에 저장됐거나, 출처에 적힌 읽은 판과 지금 원본의 내용이 다르면 newer 가 true 다.
 * 출처 파일이 없으면 빈 목록.
 */
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
  const read = readVersions(text);
  for (const file of linkedFiles(text)) {
    try {
      const source = await resolveSource(root, docAbs, file);
      if (source === null) continue; // 같은 이름의 원본이 두 곳에 있으면 고르지 않는다.
      const rel = await resolveInWorkspace(root, source);
      const abs = path.join(root, ...rel.split("/"));
      const info = await fs.stat(abs);
      let newer = info.mtimeMs > provTime;
      const recorded = read.get(file);
      if (!newer && recorded) {
        const now = `sha256:${createHash("sha256").update(await fs.readFile(abs)).digest("hex")}`;
        newer = [...recorded].some((v) => v !== now);
      }
      out.push({ path: rel, version: `${info.mtimeMs}:${info.size}`, newer });
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

/**
 * docmill 도구 `refresh_links` 를 CLI(`docmill refresh-links`)로 불러 문서 숫자를 고친다.
 * `--workspace` 로 작업 폴더를 넘겨, CLI가 실제로 여는 출처 파일·원본도 작업 폴더 안인지 직접 확인하게 한다.
 */
export function runRefresh(abs: string, cwd: string, timeoutMs = 60_000): Promise<RefreshResult> {
  return new Promise((resolve) => {
    execFile(
      "docmill",
      ["refresh-links", abs, "--workspace", cwd],
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
