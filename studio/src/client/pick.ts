/** 경로의 마지막 부분(파일 이름)을 돌려준다. 경로 구분자는 `/`다. */
export function baseName(p: string): string {
  return p.slice(p.lastIndexOf("/") + 1);
}

/** 작업 폴더 파일 중 이름이 `name`인 파일의 상대 경로를 목록 순서대로 모두 돌려준다. */
export function matchByName(files: readonly string[], name: string): string[] {
  return files.filter((f) => baseName(f) === name);
}

/** 파일 선택기·끌어다 놓기로 고른 파일을 어떻게 처리할지. */
export type PickResult =
  | { kind: "open"; path: string }
  | { kind: "choose"; paths: string[] }
  | { kind: "missing" };

/**
 * 고른 파일의 처리 방법을 정한다.
 * 실제 경로(Electron)가 있으면 그 경로를 그대로 연다. 작업 폴더 밖인지는 서버가 판단한다.
 * 실제 경로가 없으면(브라우저) 이름이 같은 파일이 하나뿐이어도 자동으로 열지 않고 사용자가 고르게 한다.
 */
export function resolvePicked(
  files: readonly string[],
  name: string,
  realPath: string | null,
): PickResult {
  if (realPath) return { kind: "open", path: realPath };
  const matches = matchByName(files, name);
  return matches.length > 0 ? { kind: "choose", paths: matches } : { kind: "missing" };
}
