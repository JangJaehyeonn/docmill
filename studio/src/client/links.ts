/** 문서와 연결된 원본 파일 상태 (`/api/links` 응답 항목). */
export interface LinkSource {
  path: string;
  version: string;
  newer: boolean;
}

/** 바뀐 위치 하나 (`/api/links/refresh` 응답의 changes 항목). */
export interface LinkChange {
  label: string;
  old: number;
  new: number;
}

/** 사용자가 이미 처리(고치기·나중에)한 원본 판: `문서|원본` → 판. */
export type Handled = Record<string, string>;

function key(doc: string, source: string): string {
  return `${doc}|${source}`;
}

/** 문서를 만든 뒤 저장됐고 아직 처리하지 않은 원본만 고른다. */
export function changedSources(doc: string, sources: LinkSource[], handled: Handled): LinkSource[] {
  return sources.filter((s) => s.newer && handled[key(doc, s.path)] !== s.version);
}

/** 지금 보이는 원본 판을 처리한 것으로 기록한다 (같은 판으로는 다시 제안하지 않는다). */
export function markHandled(doc: string, sources: LinkSource[], handled: Handled): Handled {
  const next = { ...handled };
  for (const s of sources) next[key(doc, s.path)] = s.version;
  return next;
}

function fmt(n: number): string {
  return n.toLocaleString("ko-KR", { maximumFractionDigits: 2 });
}

/** 바뀐 위치 한 줄 문구: `슬라이드 2 도형 3 · 합계: 10,150 → 10,450`. */
export function changeLine(c: LinkChange): string {
  return `${c.label}: ${fmt(c.old)} → ${fmt(c.new)}`;
}

/** 파일 경로의 이름 부분. */
export function baseName(p: string): string {
  return p.slice(p.lastIndexOf("/") + 1);
}
