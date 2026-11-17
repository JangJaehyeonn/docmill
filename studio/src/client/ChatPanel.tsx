import { useEffect, useRef, useState } from "react";
import { applyEvent, sendChat, type ChatEvent, type ChatItem } from "./chat";

interface Suggestion {
  label: string;
  tool: string;
  message: string;
}

/** 오른쪽 채팅 패널. 결과로 바뀌거나 생긴 파일 경로는 `onPaths` 로 알린다. */
export function ChatPanel(props: {
  tabs: string[];
  active: string | null;
  onPaths: (paths: string[]) => void;
}) {
  const [items, setItems] = useState<ChatItem[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const counter = useRef(0);
  const pathsRef = useRef(props.onPaths);
  pathsRef.current = props.onPaths;

  // 활성 파일이 바뀌면 그 파일의 작업 제안을 불러온다 (규칙 기반, AI 없음).
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  useEffect(() => {
    const path = props.active;
    setSuggestions([]);
    if (!path) return;
    let stale = false;
    fetch(`/api/suggest?path=${encodeURIComponent(path)}`)
      .then((r) => (r.ok ? r.json() : { suggestions: [] }))
      .then((b: { suggestions?: Suggestion[] }) => {
        if (!stale) setSuggestions(b.suggestions ?? []);
      })
      .catch(() => undefined);
    return () => {
      stale = true;
    };
  }, [props.active]);

  const submit = async (override?: string): Promise<void> => {
    const message = (override ?? text).trim();
    if (!message || busy) return;
    if (override === undefined) setText("");
    setError(null);
    setBusy(true);
    setItems((s) => [...s, { kind: "user", id: ++counter.current, text: message }]);
    const onEvent = (e: ChatEvent): void => {
      if (e.type === "error") setError(e.message);
      else if (e.type === "tool_result" && e.paths.length > 0) pathsRef.current(e.paths);
      const id = ++counter.current;
      setItems((s) => applyEvent(s, e, id));
    };
    await sendChat({ message, tabs: props.tabs, active: props.active }, onEvent);
    setBusy(false);
  };

  return (
    <aside className="chat" aria-label="AI 채팅">
      <h2>AI 채팅</h2>
      {suggestions.length > 0 && (
        <div className="suggestions" role="group" aria-label="작업 제안">
          {suggestions.map((s) => (
            <button
              key={s.tool + s.label}
              type="button"
              disabled={busy}
              title={s.message}
              onClick={() => void submit(s.message)}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}
      <div className="chat-log" role="log" aria-live="polite">
        {items.map((it) =>
          it.kind === "user" ? (
            <div key={it.id} data-role="user" className="bubble user">
              {it.text}
            </div>
          ) : it.kind === "tool" ? (
            <details key={it.id} data-role="tool-card" className="tool-card">
              <summary>도구 호출: {it.name}</summary>
              <pre>{it.result ?? "실행 중…"}</pre>
            </details>
          ) : (
            <div key={it.id} data-role="assistant" className="bubble assistant">
              {it.text}
            </div>
          ),
        )}
        {busy && <p className="hint">에이전트가 작업 중이에요…</p>}
      </div>
      {error && (
        <div role="alert" className="alert">
          {error}
        </div>
      )}
      <form
        className="chat-form"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label>
          메시지
          <textarea value={text} rows={3} onChange={(e) => setText(e.target.value)} />
        </label>
        <button type="submit" disabled={busy}>
          보내기
        </button>
      </form>
    </aside>
  );
}
