/** 채팅 화면에 쌓이는 항목. */
export type ChatItem =
  | { kind: "user"; id: number; text: string }
  | { kind: "tool"; id: number; toolId: string; name: string; result?: string }
  | { kind: "assistant"; id: number; text: string }
  | {
      kind: "preview";
      id: number;
      previewId: string;
      path: string;
      summary: string[];
      status: PreviewStatus;
    };

/** 미리보기 카드 상태: 결정 대기 / 처리 중 / 적용됨 (취소하면 카드가 사라진다). */
export type PreviewStatus = "pending" | "working" | "applied";

/** 서버가 흘려보내는 채팅 이벤트 (studio/src/server/chat.ts 와 같은 모양). */
export type ChatEvent =
  | { type: "tool"; id: string; name: string; input: unknown }
  | { type: "tool_result"; id: string; text: string; paths: string[] }
  | { type: "preview"; id: string; path: string; summary: string[] }
  | { type: "assistant"; text: string }
  | { type: "error"; message: string }
  | { type: "done" };

/** 이벤트 하나를 항목 목록에 반영한 새 목록을 돌려준다. */
export function applyEvent(items: ChatItem[], event: ChatEvent, nextId: number): ChatItem[] {
  if (event.type === "tool") {
    return [...items, { kind: "tool", id: nextId, toolId: event.id, name: event.name }];
  }
  if (event.type === "tool_result") {
    return items.map((it) =>
      it.kind === "tool" && it.toolId === event.id ? { ...it, result: event.text } : it,
    );
  }
  if (event.type === "preview") {
    const card: ChatItem = {
      kind: "preview",
      id: nextId,
      previewId: event.id,
      path: event.path,
      summary: event.summary,
      status: "pending",
    };
    return [...items, card];
  }
  if (event.type === "assistant") {
    return [...items, { kind: "assistant", id: nextId, text: event.text }];
  }
  return items;
}

/** NDJSON 조각을 줄 단위 이벤트로 나눈다. 끝의 덜 온 줄은 `rest` 로 돌려준다. */
export function splitEvents(buffer: string): { events: ChatEvent[]; rest: string } {
  const lines = buffer.split("\n");
  const rest = lines.pop() ?? "";
  const events: ChatEvent[] = [];
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      events.push(JSON.parse(line) as ChatEvent);
    } catch {
      // 깨진 줄은 무시한다.
    }
  }
  return { events, rest };
}

/** 메시지를 서버에 보내고 이벤트를 하나씩 `onEvent` 로 넘긴다. 실패하면 error 이벤트로 알린다. */
export async function sendChat(
  body: { message: string; tabs: string[]; active: string | null },
  onEvent: (e: ChatEvent) => void,
): Promise<void> {
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) {
      const err = (await res.json().catch(() => ({}))) as { error?: string };
      onEvent({ type: "error", message: err.error ?? `에이전트를 호출하지 못했어요 (${res.status}).` });
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const split = splitEvents(buffer);
      buffer = split.rest;
      split.events.forEach(onEvent);
    }
    splitEvents(buffer + "\n").events.forEach(onEvent);
  } catch {
    onEvent({ type: "error", message: "에이전트와 연결이 끊겼어요. 다시 시도하세요." });
  }
}

/** `/api/preflight` 응답 모양. */
export interface PreflightBody {
  checks: { name: string; status: string; count: number }[];
  error?: string;
}

/** 점검 결과를 채팅에 보일 줄 단위 글자로 만든다 (항목: 상태 · 위치 n곳). */
export function formatPreflight(body: PreflightBody): string {
  if (body.error) return `보내기 전 점검 실패: ${body.error}`;
  const lines = body.checks.map(
    (c) => `${c.name}: ${c.status}${c.count > 0 ? ` (${c.count}곳)` : ""}`,
  );
  return ["보내기 전 점검 결과", ...lines].join("\n");
}

export const PREFLIGHT_UNSAVED_NOTE = "저장하지 못해 마지막으로 저장된 파일을 점검했어요.";
export const PREFLIGHT_FAILED = "저장하지 못했거나 점검을 실행하지 못했어요. 저장한 뒤 다시 시도해 주세요.";

/**
 * 먼저 저장을 시도하고, 저장 결과와 상관없이 디스크 파일로 점검해 채팅 글자를 만든다.
 * 저장에 실패하면 결과 위에 안내 한 줄을 붙이고, 점검 요청 자체가 실패할 때만 오류 문구를 쓴다.
 */
export async function preflightText(
  path: string,
  save: (path: string) => Promise<boolean>,
  fetchFn: typeof fetch = fetch,
): Promise<string> {
  let saved: boolean;
  try {
    saved = await save(path);
  } catch {
    saved = false;
  }
  let body: string;
  try {
    const res = await fetchFn(`/api/preflight?path=${encodeURIComponent(path)}`);
    body = formatPreflight((await res.json()) as PreflightBody);
  } catch {
    return PREFLIGHT_FAILED;
  }
  return saved ? body : `${PREFLIGHT_UNSAVED_NOTE}\n${body}`;
}

/** 미리보기 카드의 상태를 바꾼다. */
export function setPreviewStatus(
  items: ChatItem[],
  previewId: string,
  status: PreviewStatus,
): ChatItem[] {
  return items.map((it) =>
    it.kind === "preview" && it.previewId === previewId ? { ...it, status } : it,
  );
}

/** 미리보기를 적용(apply)하거나 취소(cancel)한다. 성공하면 true. */
export async function decidePreview(action: "apply" | "cancel", id: string): Promise<boolean> {
  try {
    const res = await fetch(`/api/preview/${action}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id }),
    });
    return res.ok;
  } catch {
    return false;
  }
}
