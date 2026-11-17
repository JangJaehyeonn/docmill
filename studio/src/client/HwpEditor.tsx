import { createStudio, type RhwpEditor } from "@rhwp/editor";
import { useEffect, useRef, useState } from "react";
import type { EditorState } from "./OfficeEditor";
import { makeHandle, registerHwpEditor, unregisterHwpEditor } from "./hwp";

/**
 * 한글 편집기 한 개. studio 서버의 `/rhwp/` 에서 받은 rhwp 편집기를 iframe 으로 연다.
 * 탭을 닫을 때까지 마운트된 채로 남고, 활성 탭이 아니면 숨긴다 (저장 안 한 편집 내용을 지키기 위해).
 */
export function HwpEditor(props: {
  path: string;
  active: boolean;
  onState: (state: EditorState) => void;
  onPages: (pages: number) => void;
}) {
  const { path, active, onState, onPages } = props;
  const [ready, setReady] = useState<RhwpEditor | null>(null);
  const holder = useRef<HTMLDivElement>(null);
  const notifyState = useRef(onState);
  notifyState.current = onState;
  const notifyPages = useRef(onPages);
  notifyPages.current = onPages;

  useEffect(() => {
    let editor: RhwpEditor | null = null;
    let cancelled = false;
    notifyState.current("loading");
    void (async () => {
      try {
        if (!holder.current) return;
        const created = await createStudio(holder.current, {
          studioUrl: `${window.location.origin}/rhwp/`,
          plugins: ["hwpctrl"],
        });
        editor = created;
        if (cancelled) {
          created.destroy();
          return;
        }
        const res = await fetch(`/api/hwp?path=${encodeURIComponent(path)}`);
        if (!res.ok) throw new Error("file");
        const loaded = await created.loadFile(await res.arrayBuffer(), path.split("/").pop());
        if (cancelled) return;
        registerHwpEditor(path, created);
        setReady(created);
        notifyPages.current(loaded.pageCount);
        notifyState.current("ready");
      } catch {
        if (!cancelled) notifyState.current("unavailable");
      }
    })();
    return () => {
      cancelled = true;
      if (editor) {
        unregisterHwpEditor(path, editor);
        editor.destroy();
      }
    };
  }, [path]);

  // 활성 탭의 편집기만 window.rhwpEditor 로 노출한다.
  useEffect(() => {
    if (!active || !ready) return;
    const w = window as unknown as { rhwpEditor?: unknown };
    const handle = makeHandle(ready);
    w.rhwpEditor = handle;
    return () => {
      if (w.rhwpEditor === handle) delete w.rhwpEditor;
    };
  }, [active, ready]);

  return <div ref={holder} className="hwp-editor" hidden={!active} />;
}
