import { execFile } from "node:child_process";

/** 비슷한 문서 하나: 작업 폴더 기준 경로와 제목. */
export interface SimilarDoc {
  path: string;
  title: string;
}

/** `docmill similar` 출력 JSON 에서 목록을 꺼낸다. 형식이 맞지 않으면 빈 목록. */
export function parseSimilar(stdout: string): SimilarDoc[] {
  let data: { similar?: unknown };
  try {
    data = JSON.parse(stdout) as typeof data;
  } catch {
    return [];
  }
  if (!Array.isArray(data.similar)) return [];
  const out: SimilarDoc[] = [];
  for (const s of data.similar as Record<string, unknown>[]) {
    if (typeof s?.path === "string" && typeof s.title === "string") out.push({ path: s.path, title: s.title });
  }
  return out;
}

/** docmill CLI(`docmill similar`)로 비슷한 문서를 찾는다. 실패하면 빈 목록. */
export function fetchSimilar(workspace: string, query: string, timeoutMs = 30_000): Promise<SimilarDoc[]> {
  return new Promise((resolve) => {
    execFile(
      "docmill",
      ["similar", workspace, query],
      { timeout: timeoutMs, encoding: "utf-8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (err, stdout) => resolve(err ? [] : parseSimilar(stdout)),
    );
  });
}
