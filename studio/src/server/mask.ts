import { execFile } from "node:child_process";

/** 자리표 → 원래 값 대응표. 서버 메모리에만 두고 로그·파일에 쓰지 않는다. */
export type MaskTable = Record<string, string>;

/** 에이전트에 넘길 가려진 내용. */
export interface MaskedInput {
  documents: { path: string; text: string }[];
  message: string;
  table: MaskTable;
}

const PLACEHOLDER = /\[(?:주민번호|전화번호|이메일|계좌번호|이름)\d+\]/g;

/** 문자열 안의 자리표를 대응표의 원래 값으로 되돌린다. 대응표에 없으면 그대로 둔다. */
export function unmaskText(text: string, table: MaskTable): string {
  return text.replace(PLACEHOLDER, (p) => table[p] ?? p);
}

/** `docmill` CLI 를 불러 stdin 으로 JSON 을 넘기고 stdout 을 돌려준다. 실패하면 null. */
function runDocmill(args: string[], input: unknown, timeoutMs: number): Promise<string | null> {
  return new Promise((resolve) => {
    const child = execFile(
      "docmill",
      args,
      { timeout: timeoutMs, encoding: "utf-8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (err, stdout) => resolve(err ? null : stdout),
    );
    child.stdin?.on("error", () => undefined);
    child.stdin?.end(JSON.stringify(input));
  });
}

/**
 * docmill 도구 `mask_text` 의 문서 버전(`docmill mask-doc`)으로 문서 내용과 메시지의 개인정보를 가린다.
 * 실패하면 null — 호출한 쪽은 원문을 에이전트에 넘기지 않고 중단해야 한다.
 */
export async function maskInput(
  req: { paths: string[]; message: string; names: string[]; table: MaskTable },
  timeoutMs = 60_000,
): Promise<MaskedInput | null> {
  const out = await runDocmill(["mask-doc"], req, timeoutMs);
  if (out === null) return null;
  try {
    const data = JSON.parse(out) as Partial<MaskedInput>;
    if (!Array.isArray(data.documents) || typeof data.message !== "string" || !data.table) return null;
    return { documents: data.documents, message: data.message, table: data.table };
  } catch {
    return null;
  }
}

/** 에이전트가 쓴 문서 파일의 자리표를 원래 값으로 되돌린다 (`docmill unmask-doc`). */
export async function unmaskFile(abs: string, table: MaskTable, timeoutMs = 60_000): Promise<boolean> {
  if (Object.keys(table).length === 0) return true;
  return (await runDocmill(["unmask-doc", abs], table, timeoutMs)) !== null;
}

/** 환경 변수 `DOCMILL_STUDIO_MASK_NAMES`(쉼표로 구분)에서 사람이 등록한 이름 목록을 읽는다. */
export function registeredNames(env: NodeJS.ProcessEnv): string[] {
  return (env.DOCMILL_STUDIO_MASK_NAMES ?? "")
    .split(",")
    .map((n) => n.trim())
    .filter((n) => n !== "");
}
