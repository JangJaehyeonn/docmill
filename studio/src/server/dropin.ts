import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";

/** 범위 넣기 결과: 요약과 새 슬라이드 번호 또는 실패 이유. */
export interface InsertResult {
  summary: string;
  slide?: number;
  numbers?: number;
  error?: string;
}

/** 엑셀 셀 범위(`A1:C16`, `B2`)의 모양만 확인한다. 실제 해석은 docmill 도구가 한다. */
export function isCellRange(text: string): boolean {
  return /^\$?[A-Za-z]{1,3}\$?\d{1,7}(:\$?[A-Za-z]{1,3}\$?\d{1,7})?$/.test(text.trim());
}

/** `docmill insert-range` 가 출력한 JSON 을 결과로 바꾼다. 형식이 맞지 않으면 error. */
export function parseInsert(stdout: string): InsertResult {
  const failed = (error: string): InsertResult => ({ summary: "", error });
  let data: Record<string, unknown>;
  try {
    const parsed: unknown = JSON.parse(stdout);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return failed("범위를 넣은 결과를 읽지 못했어요.");
    }
    data = parsed as Record<string, unknown>;
  } catch {
    return failed("범위를 넣은 결과를 읽지 못했어요.");
  }
  if (typeof data.error === "string") return failed(data.error);
  if (typeof data.summary !== "string") return failed("범위를 넣은 결과를 읽지 못했어요.");
  return {
    summary: data.summary,
    slide: typeof data.slide === "number" ? data.slide : undefined,
    numbers: typeof data.numbers === "number" ? data.numbers : undefined,
  };
}

/** 응답을 잃었지만 파일 두 개(문서·출처)가 모두 바뀐 삽입을 성공으로 알릴 때의 요약. */
export const LATE_SUMMARY = "범위를 넣었어요. 응답이 늦게 와서 자세한 결과는 표시하지 못했어요.";

async function digest(file: string): Promise<string> {
  try {
    return createHash("sha256").update(await fs.readFile(file)).digest("hex");
  } catch {
    return "";
  }
}

function run(cwd: string, args: string[], timeoutMs: number): Promise<{ ok: boolean; stdout: string }> {
  return new Promise((resolve) => {
    execFile(
      "docmill",
      args,
      { cwd, timeout: timeoutMs, encoding: "utf-8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (err, stdout) => resolve({ ok: !err, stdout: String(stdout ?? "") }),
    );
  });
}

/**
 * docmill 도구 `insert_range` 를 CLI(`docmill insert-range`)로 불러 범위를 문서에 표로 넣는다.
 *
 * 응답을 잃으면(시간 초과로 끝남 등) 곧바로 `docmill recover-insert` 로 반쯤 발행된 삽입을 되돌린다.
 * 그 뒤 문서와 출처 파일이 둘 다 바뀌어 있으면 삽입은 끝난 것이므로 성공으로 알려, 다시 시도해
 * 표가 겹치지 않게 한다.
 */
export async function runInsert(
  docAbs: string,
  sourceAbs: string,
  range: string,
  cwd: string,
  sheet = "",
  timeoutMs = 60_000,
): Promise<InsertResult> {
  const args = ["insert-range", docAbs, sourceAbs, range.trim()];
  if (sheet !== "") args.push("--sheet", sheet); // keep the exact name: sheets may have edge spaces
  const prov = `${docAbs}.provenance.json`;
  const before = [await digest(docAbs), await digest(prov)];
  const r = await run(cwd, args, timeoutMs);
  if (r.ok) return parseInsert(r.stdout);
  await run(cwd, ["recover-insert", docAbs], 30_000);
  const after = [await digest(docAbs), await digest(prov)];
  if (after[0] !== before[0] && after[1] !== before[1]) return { summary: LATE_SUMMARY };
  return { summary: "", error: "범위를 문서에 넣지 못했어요." };
}
