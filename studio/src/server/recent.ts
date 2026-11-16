import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

export const MAX_RECENT = 20;

interface RecentEntry {
  workspace: string;
  path: string;
}

/** 최근 파일 저장 위치: $DOCMILL_HOME/studio/recent.json (없으면 ~/.docmill). */
export function recentStorePath(env: NodeJS.ProcessEnv = process.env): string {
  const home = env.DOCMILL_HOME || path.join(os.homedir(), ".docmill");
  return path.join(home, "studio", "recent.json");
}

async function readAll(file: string): Promise<RecentEntry[]> {
  try {
    const data: unknown = JSON.parse(await fs.readFile(file, "utf-8"));
    return Array.isArray(data) ? (data as RecentEntry[]) : [];
  } catch {
    return [];
  }
}

/** 이 작업 폴더의 최근 파일 (가장 최근이 맨 앞). */
export async function listRecent(file: string, workspace: string): Promise<string[]> {
  return (await readAll(file)).filter((e) => e.workspace === workspace).map((e) => e.path);
}

/** 파일을 최근 목록 맨 앞에 올린다 (중복은 합친다). */
export async function addRecent(file: string, workspace: string, rel: string): Promise<void> {
  const rest = (await readAll(file)).filter((e) => !(e.workspace === workspace && e.path === rel));
  const next = [{ workspace, path: rel }, ...rest].slice(0, MAX_RECENT);
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, JSON.stringify(next, null, 2), "utf-8");
}
