import { execFile } from "node:child_process";

/** 제안 하나: 버튼 문구, docmill 도구 이름, 채팅으로 보낼 문구. */
export interface Suggestion {
  label: string;
  tool: string;
  message: string;
}

/** `docmill suggest` 가 출력한 JSON 에서 제안 목록을 꺼낸다. 형식이 맞지 않으면 빈 목록. */
export function parseSuggestions(stdout: string): Suggestion[] {
  let data: { suggestions?: unknown };
  try {
    data = JSON.parse(stdout) as typeof data;
  } catch {
    return [];
  }
  if (!Array.isArray(data.suggestions)) return [];
  const out: Suggestion[] = [];
  for (const s of data.suggestions as Record<string, unknown>[]) {
    if (typeof s?.label === "string" && typeof s.tool === "string" && typeof s.message === "string") {
      out.push({ label: s.label, tool: s.tool, message: s.message });
    }
  }
  return out.slice(0, 3);
}

/** docmill 도구 `suggest_actions` 를 CLI(`docmill suggest`)로 불러 제안을 받는다. 실패하면 빈 목록. */
export function fetchSuggestions(abs: string, timeoutMs = 30_000): Promise<Suggestion[]> {
  return new Promise((resolve) => {
    execFile(
      "docmill",
      ["suggest", abs],
      { timeout: timeoutMs, encoding: "utf-8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (err, stdout) => resolve(err ? [] : parseSuggestions(stdout)),
    );
  });
}
