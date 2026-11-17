import { useCallback, useEffect, useRef, useState } from "react";
import { HwpEditor } from "./HwpEditor";
import { beginSave, endSave, exportHwp, isHwpPath, notifyHwpSaved } from "./hwp";
import { NO_SERVER_NOTICE, OfficeEditor, type EditorState } from "./OfficeEditor";
import { resolvePicked } from "./pick";
import { afterRefresh, readOpenParam } from "./startup";
import { closeTab, openTab, type TabState } from "./tabs";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} 요청 실패 (${res.status})`);
  return (await res.json()) as T;
}

/** Electron preload 가 노출한 실제 경로 조회 함수. 브라우저에서는 없다. */
function realPathOf(file: File): string | null {
  const bridge = (window as { docmillStudio?: { pathForFile?: (f: File) => string } })
    .docmillStudio;
  return bridge?.pathForFile?.(file) || null;
}

const LOAD_ERROR = "작업 폴더 목록을 불러오지 못했습니다. 다시 시도하세요.";

/** studio 화면: 위쪽 탭, 왼쪽 작업 폴더 파일 목록, 가운데 시작 화면(최근 파일). */
export function App() {
  const [files, setFiles] = useState<string[]>([]);
  const [recent, setRecent] = useState<string[]>([]);
  const [tabState, setTabState] = useState<TabState>({ tabs: [], active: null });
  const [error, setError] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [choices, setChoices] = useState<string[]>([]);
  const [editorStates, setEditorStates] = useState<Record<string, EditorState>>({});
  const [pageCounts, setPageCounts] = useState<Record<string, number>>({});
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [savingPaths, setSavingPaths] = useState<Record<string, boolean>>({});
  const picker = useRef<HTMLInputElement>(null);
  // 시작 주소의 ?open= 은 목록 조회가 처음 성공했을 때 한 번만 연다.
  const pendingOpen = useRef<string | null>(readOpenParam(window.location.search));

  const refresh = useCallback(async () => {
    const [f, r] = await Promise.all([
      getJson<{ files: string[] }>("/api/files"),
      getJson<{ recent: string[] }>("/api/recent"),
    ]);
    setFiles(f.files);
    setRecent(r.recent);
    setLoadFailed(false);
    return f.files;
  }, []);

  const refreshOrReport = useCallback(async (): Promise<boolean> => {
    try {
      await refresh();
      return true;
    } catch {
      setLoadFailed(true);
      setError(LOAD_ERROR);
      return false;
    }
  }, [refresh]);

  const openPath = useCallback(
    async (path: string) => {
      const res = await fetch("/api/open", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path }),
      });
      const body = (await res.json()) as { path?: string; name?: string; error?: string };
      if (!res.ok || !body.path || !body.name) {
        setError(body.error ?? "파일을 열 수 없습니다.");
        return;
      }
      setError(null);
      setChoices([]);
      const file = { path: body.path, name: body.name };
      setTabState((s) => openTab(s, file));
      await refreshOrReport();
    },
    [refreshOrReport],
  );

  // 목록을 (다시) 불러오고, 아직 처리하지 못한 시작 open 이 있을 때만 그 파일을 연다.
  const load = useCallback(async () => {
    const ok = await refreshOrReport();
    const step = afterRefresh(pendingOpen.current, ok);
    pendingOpen.current = step.pending;
    if (!ok) return;
    setError(null);
    if (step.open) await openPath(step.open);
  }, [refreshOrReport, openPath]);

  useEffect(() => {
    void load();
  }, [load]);

  // 고른 파일의 실제 경로(Electron)가 있으면 그대로 서버에 넘겨 작업 폴더 밖이면 거부되게 한다.
  // 브라우저는 실제 경로를 알려 주지 않으므로 이름이 같은 작업 폴더 파일을 자동으로 열지 않고 고르게 한다.
  const openPicked = (file: File): void => {
    const result = resolvePicked(files, file.name, realPathOf(file));
    if (result.kind === "open") {
      setChoices([]);
      void openPath(result.path);
    } else if (result.kind === "choose") {
      setError(null);
      setChoices(result.paths);
    } else {
      setChoices([]);
      setError(`'${file.name}' 파일은 작업 폴더에 없습니다. 작업 폴더 안의 파일만 열 수 있습니다.`);
    }
  };

  const onDrop = (e: React.DragEvent): void => {
    e.preventDefault();
    const dropped = e.dataTransfer.files[0];
    if (dropped) openPicked(dropped);
  };

  const active = tabState.tabs.find((t) => t.path === tabState.active);
  const activeOffice = active ? /\.(xlsx|pptx|docx)$/i.test(active.path) : false;
  const activeHwp = active ? isHwpPath(active.path) : false;
  const activeEditing = activeOffice || activeHwp;
  const activeState = active ? editorStates[active.path] : undefined;

  const saveHwp = async (path: string): Promise<void> => {
    // 같은 문서의 저장은 한 번에 하나만: 늦게 끝난 옛 저장이 최신 내용을 덮지 않게 한다.
    if (!beginSave(path)) return;
    setSavingPaths((m) => ({ ...m, [path]: true }));
    setSaveMessage("저장 중…");
    try {
      const { editor, bytes } = await exportHwp(path);
      const res = await fetch(`/api/hwp?path=${encodeURIComponent(path)}`, {
        method: "PUT",
        headers: { "content-type": "application/octet-stream" },
        body: bytes as BodyInit,
      });
      if (!res.ok) throw new Error("save");
      await notifyHwpSaved(path, editor);
      setSaveMessage("저장했어요. 작업 폴더 파일에 반영됐어요.");
    } catch {
      setSaveMessage("저장하지 못했어요.");
    } finally {
      endSave(path);
      setSavingPaths((m) => ({ ...m, [path]: false }));
    }
  };

  const save = async (path: string): Promise<void> => {
    if (isHwpPath(path)) return saveHwp(path);
    setSaveMessage("저장 요청 중…");
    try {
      const res = await fetch(`/api/save?path=${encodeURIComponent(path)}`, { method: "POST" });
      const body = (await res.json()) as { error?: string };
      setSaveMessage(res.ok ? "저장을 요청했어요. 곧 작업 폴더 파일에 반영돼요." : (body.error ?? "저장하지 못했어요."));
    } catch {
      setSaveMessage("저장하지 못했어요.");
    }
  };

  return (
    <div className="app" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <header className="topbar">
        <div role="tablist" aria-label="열린 파일" className="tabs">
          {tabState.tabs.map((t) => (
            <span key={t.path} className={t.path === tabState.active ? "tab active" : "tab"}>
              <button
                role="tab"
                aria-selected={t.path === tabState.active}
                title={t.path}
                onClick={() => setTabState((s) => openTab(s, t))}
              >
                {t.name}
              </button>
              <button
                className="close"
                aria-label={`탭 닫기: ${t.name}`}
                onClick={() => setTabState((s) => closeTab(s, t.path))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      </header>

      {error && (
        <div role="alert" className="alert">
          {error}
          {loadFailed && (
            <button type="button" onClick={() => void load()}>
              다시 시도
            </button>
          )}
        </div>
      )}

      {choices.length > 0 && (
        <section role="region" aria-label="열 파일 고르기" className="alert">
          <p>
            고른 파일의 실제 위치를 확인할 수 없습니다. 작업 폴더에서 이름이 같은 파일을 찾았으니
            열 파일을 직접 고르세요.
          </p>
          <ul>
            {choices.map((f) => (
              <li key={f}>
                <button type="button" className="file" onClick={() => void openPath(f)}>
                  {f}
                </button>
              </li>
            ))}
          </ul>
          <button type="button" onClick={() => setChoices([])}>
            취소
          </button>
        </section>
      )}

      <div className="body">
        <aside className="sidebar">
          <button className="primary" onClick={() => picker.current?.click()}>
            파일 열기
          </button>
          <input
            ref={picker}
            type="file"
            hidden
            aria-hidden="true"
            tabIndex={-1}
            accept=".xlsx,.pptx,.docx,.hwp,.hwpx"
            onChange={(e) => {
              const picked = e.target.files?.[0];
              if (picked) openPicked(picked);
              e.target.value = "";
            }}
          />
          <h2>작업 폴더 파일</h2>
          <ul aria-label="작업 폴더 파일">
            {files.map((f) => (
              <li key={f}>
                <button className="file" onClick={() => void openPath(f)}>
                  {f}
                </button>
              </li>
            ))}
          </ul>
          {files.length === 0 && <p className="hint">작업 폴더에 문서 파일이 없습니다.</p>}
        </aside>

        <main
          className={activeEditing ? "main editing" : "main"}
          role="tabpanel"
          data-editor-state={activeEditing ? activeState : undefined}
        >
          {active && activeHwp ? (
            <>
              <div className="editor-bar">
                <button
                  type="button"
                  disabled={savingPaths[active.path] === true}
                  onClick={() => void save(active.path)}
                >
                  저장
                </button>
                {pageCounts[active.path] !== undefined && (
                  <span>쪽 수: {pageCounts[active.path]}쪽</span>
                )}
                {saveMessage && <span className="hint">{saveMessage}</span>}
              </div>
              {activeState === "unavailable" && (
                <p role="status" className="alert">
                  한글 편집기를 불러오지 못했어요. rhwp 편집기 빌드(RHWP_STUDIO_DIST)를 확인한 뒤 다시 열어 주세요.
                </p>
              )}
            </>
          ) : active && activeOffice ? (
            <>
              <div className="editor-bar">
                <button type="button" onClick={() => void save(active.path)}>
                  저장
                </button>
                {saveMessage && <span className="hint">{saveMessage}</span>}
              </div>
              {activeState === "unavailable" && (
                <p role="status" className="alert">
                  {NO_SERVER_NOTICE}. 문서 서버 주소(DOCMILL_ONLYOFFICE_URL)를 확인한 뒤 다시 열어 주세요.
                </p>
              )}
              <OfficeEditor
                key={active.path}
                path={active.path}
                onState={(s) => setEditorStates((m) => ({ ...m, [active.path]: s }))}
              />
            </>
          ) : active ? (
            <p className="hint">{active.name} — 문서 화면은 다음 단계에서 연결됩니다.</p>
          ) : (
            <section aria-labelledby="recent-title" role="region" aria-label="최근 파일">
              <h1>docmill studio</h1>
              <h2 id="recent-title">최근 파일</h2>
              <p className="hint">목록에서 파일을 고르거나 이 화면으로 끌어다 놓으세요.</p>
              <ul>
                {recent.map((f) => (
                  <li key={f}>
                    <button className="file" onClick={() => void openPath(f)}>
                      {f}
                    </button>
                  </li>
                ))}
              </ul>
              {recent.length === 0 && <p className="hint">아직 연 파일이 없습니다.</p>}
            </section>
          )}
          {/* 열린 한글 탭은 닫을 때까지 편집기를 유지하고, 활성 탭이 아니면 숨긴다. */}
          {tabState.tabs
            .filter((t) => isHwpPath(t.path))
            .map((t) => (
              <HwpEditor
                key={t.path}
                path={t.path}
                active={t.path === tabState.active}
                onState={(st) => setEditorStates((m) => ({ ...m, [t.path]: st }))}
                onPages={(n) => setPageCounts((m) => ({ ...m, [t.path]: n }))}
              />
            ))}
        </main>
      </div>
    </div>
  );
}
