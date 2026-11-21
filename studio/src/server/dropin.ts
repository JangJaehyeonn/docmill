import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

/** 범위 넣기 결과: 요약과 새 슬라이드 번호 또는 실패 이유. */
export interface InsertResult {
  summary: string;
  slide?: number;
  numbers?: number;
  error?: string;
  /** 끊긴 삽입을 정리하지 못해 편집기를 잠근 채 두어야 할 때 true. */
  locked?: boolean;
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

/** 응답을 잃었지만 이 호출이 두 파일 발행을 마친 삽입을 성공으로 알릴 때의 요약. */
export const LATE_SUMMARY = "범위를 넣었어요. 응답이 늦게 와서 자세한 결과는 표시하지 못했어요.";

/** 응답을 잃은 뒤 복구까지 실패했을 때의 안내. 편집기는 잠긴 채로 둔다. */
export const RECOVER_FAILED =
  "범위를 넣다가 끊겼고 문서를 정리하지 못했어요. 탭을 닫고 다시 열어 주세요 (열 때 다시 정리해요).";

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

/** 복구 결과 상태: none(할 일 없음)·rolled_back(되돌림)·complete(지난 삽입이 다 발행됨)·failed. */
export type RecoverState = "none" | "rolled_back" | "complete" | "failed";

/**
 * `docmill recover-insert` 로 끊긴 삽입을 문서 잠금 안에서 정리한다.
 * receipt를 주면 `mine` 은 정리한 저널이 그 완료 표시 경로를 넘긴 호출의 것인지다.
 */
export async function runRecover(
  docAbs: string,
  cwd: string,
  receipt = "",
): Promise<{ state: RecoverState; mine: boolean }> {
  const args = ["recover-insert", docAbs];
  if (receipt !== "") args.push("--receipt", receipt);
  const r = await run(cwd, args, 30_000);
  try {
    const body = JSON.parse(r.stdout) as { state?: unknown; mine?: unknown };
    const state = body.state;
    if (r.ok && (state === "none" || state === "rolled_back" || state === "complete")) {
      return { state, mine: body.mine === true };
    }
  } catch {
    // fall through
  }
  return { state: "failed", mine: false };
}

/**
 * Python `_journal` 과 같은 규칙의 삽입 저널 폴더 (임시 폴더의 `docmill-insert-<실제 경로 해시 24자>`).
 * 이 폴더가 있으면 지난 삽입이 정리되지 않은 것이다.
 */
export async function hasInsertJournal(docAbs: string): Promise<boolean> {
  try {
    const real = await fs.realpath(docAbs);
    const digest = createHash("sha256").update(real, "utf8").digest("hex").slice(0, 24);
    return (await fs.stat(path.join(os.tmpdir(), `docmill-insert-${digest}`))).isDirectory();
  } catch {
    return false;
  }
}

/**
 * docmill 도구 `insert_range` 를 CLI(`docmill insert-range`)로 불러 범위를 문서에 표로 넣는다.
 *
 * `--receipt` 로 완료 표시 파일을 넘긴다. 응답을 잃으면(시간 초과 등) 곧바로 `docmill recover-insert` 로
 * 반쯤 발행된 삽입을 되돌린다. 이 호출의 완료 표시가 있거나, 복구가 이 호출의 저널이 "다 발행됨"을 알리면 성공으로
 * 알려 다시 시도해도 표가 겹치지 않게 한다. 잠금을 기다리다 끝났으면 표시가 없어 실패다.
 * 복구도 실패하면 `locked` 를 붙여 편집기를 잠근 채 둔다.
 */
export async function runInsert(
  docAbs: string,
  sourceAbs: string,
  range: string,
  cwd: string,
  sheet = "",
  timeoutMs = 60_000,
): Promise<InsertResult> {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), "docmill-receipt-"));
  const receipt = path.join(temp, "receipt.json");
  try {
    const args = ["insert-range", docAbs, sourceAbs, range.trim(), "--receipt", receipt];
    if (sheet !== "") args.push("--sheet", sheet); // keep the exact name: sheets may have edge spaces
    const r = await run(cwd, args, timeoutMs);
    if (r.ok) return parseInsert(r.stdout);
    const { state, mine } = await runRecover(docAbs, cwd, receipt);
    const published = await fs.stat(receipt).then(
      () => true,
      () => false,
    );
    // 다른 삽입의 저널이 complete로 정리됐어도 이 호출의 것이 아니면 성공으로 보지 않는다.
    if (published || (state === "complete" && mine)) return { summary: LATE_SUMMARY };
    if (state === "failed") return { summary: "", error: RECOVER_FAILED, locked: true };
    return { summary: "", error: "범위를 문서에 넣지 못했어요." };
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
}
