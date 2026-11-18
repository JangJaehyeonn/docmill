/**
 * ONLYOFFICE 편집기가 로컬 편집 내용을 문서 서버로 다 보냈는지 추적한다.
 *
 * 편집기는 `onDocumentStateChange` 로 true(아직 보내지 않은 변경 있음)·false(변경을 서버로 보냄)를 알린다.
 * 저장(강제 저장)은 서버에 닿은 변경만 파일에 담으므로, 방금 입력한 내용이 빠지지 않게
 * 저장 전에 false 가 될 때까지 기다린다.
 */
const pending = new Map<string, boolean>();
const waiters = new Map<string, Set<() => void>>();

/** 편집기가 알린 상태를 기록한다. false 가 되면 기다리던 저장을 깨운다. */
export function setOfficePending(path: string, value: boolean): void {
  pending.set(path, value);
  if (!value) {
    for (const wake of [...(waiters.get(path) ?? [])]) wake();
  }
}

/** 편집기를 닫을 때 상태를 지우고 기다리던 쪽을 깨운다. */
export function clearOfficePending(path: string): void {
  pending.delete(path);
  for (const wake of [...(waiters.get(path) ?? [])]) wake();
}

/**
 * 보내지 않은 변경이 없어질 때까지 기다린다. 이미 없으면 바로 true.
 * 시간 안에 안 되면 false (저장은 그래도 진행하고, 서버의 재시도가 나머지를 맡는다).
 */
export function waitOfficeSynced(path: string, ms: number): Promise<boolean> {
  if (pending.get(path) !== true) return Promise.resolve(true);
  return new Promise((resolve) => {
    const set = waiters.get(path) ?? new Set<() => void>();
    waiters.set(path, set);
    const finish = (ok: boolean): void => {
      clearTimeout(timer);
      set.delete(wake);
      if (set.size === 0) waiters.delete(path);
      resolve(ok);
    };
    const wake = (): void => finish(true);
    const timer = setTimeout(() => finish(false), ms);
    set.add(wake);
  });
}
