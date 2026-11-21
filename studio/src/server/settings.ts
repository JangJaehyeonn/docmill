import { promises as fs } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";

/** 작업 폴더 설정 파일(.docmill/settings.json)의 내용. */
export interface UserSettings {
  department: string;
  name: string;
  title: string;
  approval_line: string[];
  default_template: string;
  tone: string;
}

export const EMPTY_SETTINGS: UserSettings = {
  department: "",
  name: "",
  title: "",
  approval_line: [],
  default_template: "",
  tone: "",
};

function text(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/** 알 수 없는 입력을 설정 모양으로 바로잡는다 (모르는 키는 버린다). */
export function normalizeSettings(raw: unknown): UserSettings {
  const o = (typeof raw === "object" && raw !== null ? raw : {}) as Record<string, unknown>;
  const line = Array.isArray(o.approval_line) ? o.approval_line : [];
  return {
    department: text(o.department),
    name: text(o.name),
    title: text(o.title),
    approval_line: line.map(text).filter((s) => s !== ""),
    default_template: text(o.default_template),
    tone: text(o.tone),
  };
}

export function settingsFile(workspace: string): string {
  return path.join(workspace, ".docmill", "settings.json");
}

/** 설정을 읽는다. 파일이 없거나 깨졌으면 빈 설정. */
export async function readSettings(workspace: string): Promise<UserSettings> {
  try {
    return normalizeSettings(JSON.parse(await fs.readFile(settingsFile(workspace), "utf-8")));
  } catch {
    return { ...EMPTY_SETTINGS };
  }
}

/** 설정을 저장한다 (임시 파일에 쓴 뒤 바꿔치기). 저장한 값을 돌려준다. */
export async function writeSettings(workspace: string, raw: unknown): Promise<UserSettings> {
  const settings = normalizeSettings(raw);
  const file = settingsFile(workspace);
  await fs.mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${process.pid}.${randomUUID()}.tmp`;
  try {
    await fs.writeFile(tmp, JSON.stringify(settings, null, 2) + "\n", { encoding: "utf-8", flag: "wx" });
    await fs.rename(tmp, file);
  } catch (err) {
    await fs.rm(tmp, { force: true });
    throw err;
  }
  return settings;
}

/** 에이전트 입력에 넣을 설정 줄. 값이 하나도 없으면 빈 목록. */
export function settingsLines(s: UserSettings): string[] {
  const rows: [string, string][] = [
    ["부서", s.department],
    ["이름", s.name],
    ["직함", s.title],
    ["결재 라인", s.approval_line.join(" > ")],
    ["기본 양식", s.default_template],
    ["문체", s.tone],
  ];
  const filled = rows.filter(([, v]) => v !== "");
  return filled.length === 0 ? [] : ["사용자 설정:", ...filled.map(([k, v]) => `- ${k}: ${v}`)];
}
