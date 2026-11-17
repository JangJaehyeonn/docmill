import { useRef, useState } from "react";
import { applyEvent, sendChat, type ChatEvent, type ChatItem } from "./chat";

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

  const submit = async (): Promise<void> => {
    const message = text.trim();
    if (!message || busy) return;
    setText("");
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
