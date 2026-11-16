/** 경로의 마지막 부분(파일 이름)을 돌려준다. 경로 구분자는 `/`다. */
export function baseName(p: string): string {
  return p.slice(p.lastIndexOf("/") + 1);
}

/** 작업 폴더 파일 중 이름이 `name`인 파일의 상대 경로를 목록 순서대로 모두 돌려준다. */
export function matchByName(files: readonly string[], name: string): string[] {
  return files.filter((f) => baseName(f) === name);
}
