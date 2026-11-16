/** 시작 주소의 `?open=` 값을 읽는다. 없거나 비어 있으면 null. */
export function readOpenParam(search: string): string | null {
  const value = new URLSearchParams(search).get("open");
  return value ? value : null;
}

/** 목록 조회 한 번이 끝난 뒤 할 일: 지금 열 파일과 다음 조회까지 남겨 둘 보류 open. */
export interface LoadStep {
  open: string | null;
  pending: string | null;
}

/**
 * 목록 조회 결과에 따라 보류된 시작 open 을 처리할지 정한다.
 * 조회가 실패하면 보류를 그대로 남기고, 성공하면 보류된 open 을 한 번만 꺼낸다.
 * 이미 꺼낸 뒤의 재시도는 목록만 갱신하므로 활성 탭과 최근 파일 순서가 바뀌지 않는다.
 */
export function afterRefresh(pending: string | null, ok: boolean): LoadStep {
  if (!ok) return { open: null, pending };
  return { open: pending, pending: null };
}
