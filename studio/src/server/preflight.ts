import { execFile } from "node:child_process";

/** 점검 항목 하나: 이름, 상태(통과·주의·실패), 위치 개수. */
export interface PreflightCheck {
  name: string;
  status: string;
  count: number;
}

/** 점검 결과: 항목 목록 또는 실패 이유. */
export interface PreflightResult {
  checks: PreflightCheck[];
  error?: string;
}

/** `docmill preflight` 가 출력한 JSON 을 항목 목록으로 바꾼다. 형식이 맞지 않으면 error. */
export function parsePreflight(stdout: string): PreflightResult {
  let data: { checks?: unknown; error?: unknown };
  try {
    data = JSON.parse(stdout) as typeof data;
  } catch {
    return { checks: [], error: "점검 결과를 읽지 못했어요." };
  }
  if (typeof data.error === "string") return { checks: [], error: data.error };
  if (!Array.isArray(data.checks)) return { checks: [], error: "점검 결과를 읽지 못했어요." };
  const checks: PreflightCheck[] = [];
  for (const c of data.checks as Record<string, unknown>[]) {
    if (typeof c?.name === "string" && typeof c.status === "string") {
      const locations = Array.isArray(c.locations) ? c.locations.length : 0;
      checks.push({ name: c.name, status: c.status, count: locations });
    }
  }
  return { checks };
}

/** docmill 도구 `preflight` 를 CLI(`docmill preflight`)로 불러 점검 결과를 받는다. */
export function runPreflight(abs: string, workspace?: string, timeoutMs = 60_000): Promise<PreflightResult> {
  return new Promise((resolve) => {
    execFile(
      "docmill",
      ["preflight", abs, ...(workspace ? ["--workspace", workspace] : [])],
      { timeout: timeoutMs, encoding: "utf-8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (err, stdout) =>
        resolve(err ? { checks: [], error: "보내기 전 점검을 실행하지 못했어요." } : parsePreflight(stdout)),
    );
  });
}
