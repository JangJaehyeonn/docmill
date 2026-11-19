import { execFile } from "node:child_process";

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
    data = JSON.parse(stdout) as Record<string, unknown>;
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

/** docmill 도구 `insert_range` 를 CLI(`docmill insert-range`)로 불러 범위를 문서에 표로 넣는다. */
export function runInsert(
  docAbs: string,
  sourceAbs: string,
  range: string,
  cwd: string,
  timeoutMs = 60_000,
): Promise<InsertResult> {
  return new Promise((resolve) => {
    execFile(
      "docmill",
      ["insert-range", docAbs, sourceAbs, range.trim()],
      { cwd, timeout: timeoutMs, encoding: "utf-8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (err, stdout) =>
        resolve(err ? { summary: "", error: "범위를 문서에 넣지 못했어요." } : parseInsert(stdout)),
    );
  });
}
