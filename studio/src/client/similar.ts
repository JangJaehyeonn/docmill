/** 연속 요청 중 마지막으로 시작한 요청만 결과를 반영하게 하는 순번표. */
export interface LatestGate {
  /** 새 요청을 시작하고 그 순번을 돌려준다. */
  begin(): number;
  /** 순번이 가장 최근에 시작한 요청인지 확인한다. */
  isLatest(seq: number): boolean;
}

/** 새 순번표를 만든다. */
export function createLatestGate(): LatestGate {
  let current = 0;
  return {
    begin: () => ++current,
    isLatest: (seq) => seq === current,
  };
}
