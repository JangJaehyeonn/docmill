import { useEffect, useRef } from "react";
import { clearOfficePending, setOfficePending } from "./officeSync";

interface EditorInit {
  server: string;
  config: Record<string, unknown>;
}

interface DocEditor {
  destroyEditor?: () => void;
}

type DocsApi = {
  DocEditor: new (id: string, config: Record<string, unknown>) => DocEditor;
};

/** ONLYOFFICE 문서 서버의 api.js 를 한 번만 불러온다. */
function loadApi(server: string): Promise<DocsApi> {
  const w = window as unknown as { DocsAPI?: DocsApi };
  if (w.DocsAPI) return Promise.resolve(w.DocsAPI);
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `${server}/web-apps/apps/api/documents/api.js`;
    script.onload = () => (w.DocsAPI ? resolve(w.DocsAPI) : reject(new Error("api.js")));
    script.onerror = () => reject(new Error("api.js"));
    document.head.appendChild(script);
  });
}

export const NO_SERVER_NOTICE = "문서 서버에 연결되지 않았어요";

export type EditorState = "loading" | "ready" | "unavailable";

/** ONLYOFFICE 편집기 한 개. 상태는 `onState` 로 알리고, 패널의 data 속성은 부모가 쓴다. */
export function OfficeEditor(props: {
  path: string;
  onState: (state: EditorState) => void;
  /** true 면 편집기 입력을 막는다 (범위를 이 문서에 넣는 동안 편집이 사라지지 않게). */
  locked?: boolean;
}) {
  const { path, onState, locked = false } = props;
  const holder = useRef<HTMLDivElement>(null);
  const notify = useRef(onState);
  notify.current = onState;

  useEffect(() => {
    const el = holder.current;
    if (!el) return;
    // inert 는 편집기 iframe 까지 포커스·입력을 막는다. 이미 포커스가 편집기 안이면 내보낸다.
    el.inert = locked;
    const focused = document.activeElement;
    if (locked && focused instanceof HTMLElement && el.contains(focused)) focused.blur();
  }, [locked]);

  useEffect(() => {
    let editor: DocEditor | null = null;
    let cancelled = false;
    notify.current("loading");
    void (async () => {
      try {
        const res = await fetch(`/api/editor-config?path=${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error("config");
        const init = (await res.json()) as EditorInit;
        const api = await loadApi(init.server);
        if (cancelled || !holder.current) return;
        const target = document.createElement("div");
        target.id = "onlyoffice-editor";
        holder.current.replaceChildren(target);
        editor = new api.DocEditor(target.id, {
          ...init.config,
          width: "100%",
          height: "100%",
          events: {
            onDocumentReady: () => !cancelled && notify.current("ready"),
            // true: 아직 서버로 보내지 않은 편집이 있음, false: 편집을 서버로 보냄 (저장 전 대기에 쓴다).
            onDocumentStateChange: (e: { data: boolean }) => setOfficePending(path, e.data),
          },
        });
      } catch {
        if (!cancelled) notify.current("unavailable");
      }
    })();
    return () => {
      cancelled = true;
      clearOfficePending(path);
      editor?.destroyEditor?.();
    };
  }, [path]);

  return (
    <>
      {locked && (
        <p role="status" className="hint">
          범위를 이 문서에 넣는 중이라 잠시 편집할 수 없어요.
        </p>
      )}
      <div ref={holder} className="office-editor" aria-busy={locked || undefined} />
    </>
  );
}
