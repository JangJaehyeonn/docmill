/**
 * ONLYOFFICE 편집기가 로컬 편집 내용을 문서 서버로 다 보냈는지 추적한다.
 *
 * 편집기는 `onDocumentStateChange` 로 true(아직 보내지 않은 변경 있음)·false(변경을 서버로 보냄)를 알린다.
 * 저장(강제 저장)은 서버에 닿은 변경만 파일에 담으므로, 방금 입력한 내용이 빠지지 않게
 * 저장 전에 false 가 될 때까지 기다린다.
 */
const pending = new Map<string, boolean>();
const waiters = new Map<string, Set<(ok: boolean) => void>>();
const events = new Map<string, Set<(v: boolean | null) => void>>();

/** 편집기가 알린 상태를 기록한다. false 가 되면 기다리던 저장을 깨운다. */
const traces = new Map<string, string[]>();

/** 진단용: 이 문서의 최근 편집기 상태 알림 (예: `t+120,f+480` = 열린 뒤 ms). 저장 요청에 실어 서버 로그에 남긴다. */
export function officeTrace(path: string): string {
  return (traces.get(path) ?? []).slice(-12).join(",");
}

const opened = Date.now();

export function setOfficePending(path: string, value: boolean): void {
  const trace = traces.get(path) ?? [];
  trace.push(`${value ? "t" : "f"}+${Date.now() - opened}`);
  traces.set(path, trace.slice(-24));
  pending.set(path, value);
  for (const notify of [...(events.get(path) ?? [])]) notify(value);
  if (!value) {
    for (const wake of [...(waiters.get(path) ?? [])]) wake(true);
  }
}

/** 편집기를 닫을 때 상태를 지우고 기다리던 쪽을 깨운다. */
export function clearOfficePending(path: string): void {
  pending.delete(path);
  for (const notify of [...(events.get(path) ?? [])]) notify(null);
  // 편집기가 닫힌 것이지 동기화가 끝난 것이 아니므로 false 로 깨운다.
  for (const wake of [...(waiters.get(path) ?? [])]) wake(false);
}

/** 편집기 이벤트를 기다린다: 상태가 바뀌면(true 또는 false) 그 값, 시간 안에 없으면 null. */
function nextEvent(path: string, ms: number): Promise<boolean | null> {
  return new Promise((resolve) => {
    const set = events.get(path) ?? new Set<(v: boolean | null) => void>();
    events.set(path, set);
    const finish = (v: boolean | null): void => {
      clearTimeout(timer);
      set.delete(finish);
      if (set.size === 0) events.delete(path);
      resolve(v);
    };
    const timer = setTimeout(() => finish(null), ms);
    set.add(finish);
  });
}

/** 보내지 않은 변경이 없어질 때까지(false 알림) 기다린다. 시간 안에 안 되거나 편집기가 닫히면 false. */
function untilSent(path: string, ms: number): Promise<boolean> {
  if (pending.get(path) !== true) return Promise.resolve(true);
  return new Promise((resolve) => {
    const set = waiters.get(path) ?? new Set<(ok: boolean) => void>();
    waiters.set(path, set);
    const finish = (ok: boolean): void => {
      clearTimeout(timer);
      set.delete(wake);
      if (set.size === 0) waiters.delete(path);
      resolve(ok);
    };
    const wake = (ok: boolean): void => finish(ok);
    const timer = setTimeout(() => finish(false), ms);
    set.add(wake);
  });
}

/**
 * 보내지 않은 변경이 없어질 때까지 기다린다. 시간 안에 안 되면 false
 * (저장은 그래도 진행하고, 서버의 재시도가 나머지를 맡는다).
 *
 * 편집기는 상태를 postMessage로 늦게 알린다. 셀에 입력하고 Enter를 누른 직후 저장하면 "보내지 않은 변경 있음"
 * 알림이 아직 오지 않아, 바로 저장하면 그 편집이 강제 저장에 빠진다 (#162). 그래서 대기 중인 변경이 없어
 * 보여도 `settleMs` 동안 새 알림이 오는지 지켜보고, 오면 보낼 때까지 기다린다. 다 보낸 뒤에도 문서 서버가
 * 변경을 반영할 짧은 틈(`settleMs`의 1/3)을 두고 다시 확인한다.
 */
export async function waitOfficeSynced(path: string, ms: number, settleMs = 3000): Promise<boolean> {
  const end = Date.now() + ms;
  const left = (): number => Math.max(0, end - Date.now());
  for (;;) {
    if (pending.get(path) === true) {
      if (!(await untilSent(path, left()))) return false;
      // 다 보냈다는 알림 뒤에 이어지는 편집이 있는지 잠깐 더 본다.
      const after = await nextEvent(path, Math.min(Math.floor(settleMs / 3), left()));
      if (after === true) continue;
      return true;
    }
    if (!pending.has(path) && settleMs <= 0) return true;
    const next = await nextEvent(path, Math.min(settleMs, left()));
    if (next === true) continue; // 늦게 온 "보내지 않은 변경 있음": 보낼 때까지 기다린다
    if (next === null) return true; // 지켜보는 동안 새 편집 없음
    if (left() <= 0) return pending.get(path) !== true;
  }
}

/** 보내지 않은 편집이 남아 있으면 true. 문서를 다시 불러오면 그 편집이 사라진다. */
export function hasOfficePending(path: string): boolean {
  return pending.get(path) === true;
}
