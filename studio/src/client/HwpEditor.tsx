import { createStudio, type RhwpEditor } from "@rhwp/editor";
import { useEffect, useRef } from "react";
import type { EditorState } from "./OfficeEditor";
import { makeHandle, registerHwpEditor, unregisterHwpEditor } from "./hwp";

/** 한글 편집기 한 개. studio 서버의 `/rhwp/` 에서 받은 rhwp 편집기를 iframe 으로 연다. */
export function HwpEditor(props: {
  path: string;
  onState: (state: EditorState) => void;
  onPages: (pages: number) => void;
}) {
  const { path, onState, onPages } = props;
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
        (window as unknown as { rhwpEditor?: unknown }).rhwpEditor = makeHandle(created);
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
      delete (window as unknown as { rhwpEditor?: unknown }).rhwpEditor;
    };
  }, [path]);

  return <div ref={holder} className="hwp-editor" />;
}
