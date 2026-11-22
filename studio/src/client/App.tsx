import { useCallback, useEffect, useRef, useState } from "react";
import type { SaveResult } from "./chat";
import { ChatPanel } from "./ChatPanel";
import { ExportDialog } from "./ExportDialog";
import { SettingsPanel } from "./SettingsPanel";
import { hasOfficePending, waitOfficeSynced } from "./officeSync";
import { HwpEditor } from "./HwpEditor";
import { beginSave, endSave, exportHwp, hasUnsavedHwpEdits, isHwpPath, notifyHwpSaved } from "./hwp";
import { NO_SERVER_NOTICE, OfficeEditor, type EditorState } from "./OfficeEditor";
import {
  baseName,
  changedSources,
  changeLine,
  markHandled,
  type Handled,
  type LinkChange,
  type LinkSource,
} from "./links";
import { DRAG_TYPE, canDropOn, isCellRange, type DraggedRange } from "./dropin";
import { resolvePicked } from "./pick";
import { parseSentenceRefs, sentenceAction, type SentenceRef, type SourceHighlight } from "./sentences";
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

/** 연결된 원본이 저장됐는지 확인하는 간격 (ms). */
const LINK_POLL_MS = 2000;

/** 새 숫자로 고친 결과 안내: 문서, 요약 또는 실패 문구, 바뀐 위치·주의 줄. */
interface LinkResult {
  doc: string;
  text: string;
  lines: string[];
}

const LOAD_ERROR = "작업 폴더 목록을 불러오지 못했습니다. 다시 시도하세요.";

/** studio 화면: 위쪽 탭, 왼쪽 작업 폴더 파일 목록, 가운데 시작 화면(최근 파일). */
export function App() {
  // 서버가 HTML에 넣어 준 첫 파일 목록으로 시작해 로드 직후부터 목록이 보이게 한다.
  const [files, setFiles] = useState<string[]>(
    () => (window as unknown as { __DOCMILL_FILES__?: string[] }).__DOCMILL_FILES__ ?? [],
  );
  const [recent, setRecent] = useState<string[]>([]);
  const [tabState, setTabState] = useState<TabState>({ tabs: [], active: null });
  const [error, setError] = useState<string | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [choices, setChoices] = useState<string[]>([]);
  const [editorStates, setEditorStates] = useState<Record<string, EditorState>>({});
  const [pageCounts, setPageCounts] = useState<Record<string, number>>({});
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [savingPaths, setSavingPaths] = useState<Record<string, boolean>>({});
  const [conflicts, setConflicts] = useState<string[]>([]);
  const [reloads, setReloads] = useState<Record<string, number>>({});
  const [linkAlerts, setLinkAlerts] = useState<Record<string, LinkSource[]>>({});
  const [linkResult, setLinkResult] = useState<LinkResult | null>(null);
  const [refreshing, setRefreshing] = useState<string | null>(null);
  const [rangeText, setRangeText] = useState("");
  const [sheetText, setSheetText] = useState("");
  const [insertingInto, setInsertingInto] = useState<string | null>(null);
  const [dropMessage, setDropMessage] = useState<string | null>(null);
  const [dropTarget, setDropTarget] = useState<string | null>(null);
  const [keyTarget, setKeyTarget] = useState("");
  const [showSettings, setShowSettings] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [sentenceRefs, setSentenceRefs] = useState<SentenceRef[]>([]);
  const [highlight, setHighlight] = useState<SourceHighlight | null>(null);
  const [sentenceMessage, setSentenceMessage] = useState<string | null>(null);
  const dragged = useRef<DraggedRange | null>(null);
  const droppingRef = useRef(false);
  const refreshingRef = useRef(false);
  const handledLinks = useRef<Handled>({});
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

  // 에이전트 결과의 파일: 열려 있는 탭이면 다시 불러오고, 아니면 새 탭으로 연다.
  const tabsRef = useRef(tabState);
  tabsRef.current = tabState;
  const reloadTab = useCallback((p: string) => {
    setEditorStates((m) => ({ ...m, [p]: "loading" }));
    setReloads((m) => ({ ...m, [p]: (m[p] ?? 0) + 1 }));
    setConflicts((c) => c.filter((x) => x !== p));
  }, []);
  // 새 탭 열기는 한 줄로 이어서 해, 응답이 뒤바뀌어도 마지막 결과 파일이 활성 탭이 되게 한다.
  const openChain = useRef<Promise<void>>(Promise.resolve());
  const onAgentPaths = useCallback(
    (paths: string[]) => {
      for (const p of paths) {
        if (!tabsRef.current.tabs.some((t) => t.path === p)) {
          openChain.current = openChain.current.then(() => openPath(p)).catch(() => undefined);
          continue;
        }
        // 이미 열린 파일도 같은 줄에서 활성으로 만들어, 마지막 결과가 활성 탭이 되게 한다.
        const tab = tabsRef.current.tabs.find((t) => t.path === p);
        if (tab) {
          openChain.current = openChain.current.then(() => setTabState((s) => openTab(s, tab)));
        }
        if (!isHwpPath(p)) {
          reloadTab(p);
        } else {
          // 저장하지 않은 편집이 있으면 자동으로 교체하지 않고 사용자에게 고르게 한다.
          void hasUnsavedHwpEdits(p).then((dirty) => {
            if (!dirty) reloadTab(p);
            else setConflicts((c) => (c.includes(p) ? c : [...c, p]));
          });
        }
      }
    },
    [openPath, reloadTab],
  );

  // 열린 문서와 연결된 원본(provenance)이 저장되면 [새 숫자로 고치기]를 제안한다 (FR-37).
  const tabPaths = tabState.tabs.map((t) => t.path).join("\n");
  useEffect(() => {
    const docs = tabPaths ? tabPaths.split("\n") : [];
    let stopped = false;
    let busy = false;
    const check = async (): Promise<void> => {
      if (busy) return;
      busy = true;
      const next: Record<string, LinkSource[]> = {};
      for (const doc of docs) {
        try {
          const url = `/api/links?path=${encodeURIComponent(doc)}`;
          const body = await getJson<{ sources?: LinkSource[] }>(url);
          const changed = changedSources(doc, body.sources ?? [], handledLinks.current);
          if (changed.length > 0) next[doc] = changed;
        } catch {
          // 다음 확인 때 다시 본다.
        }
      }
      busy = false;
      if (!stopped) setLinkAlerts(next);
    };
    void check();
    const timer = setInterval(() => void check(), LINK_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [tabPaths]);

  // 활성 탭 문서의 문장 출처(FR-47)를 불러온다. 출처 파일이 없으면 빈 목록이다.
  const activePath = tabState.active;
  useEffect(() => {
    setSentenceRefs([]);
    setSentenceMessage(null);
    if (!activePath) return;
    let stopped = false;
    getJson<unknown>(`/api/sentences?path=${encodeURIComponent(activePath)}`)
      .then((body) => {
        if (!stopped) setSentenceRefs(parseSentenceRefs(body));
      })
      .catch(() => undefined);
    return () => {
      stopped = true;
    };
  }, [activePath]);

  // 문장을 누르면 근거 원본 탭을 활성으로 만들고(없으면 연다) 근거 위치를 강조한다.
  const showSource = async (ref: SentenceRef): Promise<void> => {
    const action = sentenceAction(ref, tabsRef.current.tabs.map((t) => t.path));
    if (action.kind === "missing") {
      setSentenceMessage(action.message);
      return;
    }
    setHighlight(action.highlight);
    const tab = tabsRef.current.tabs.find((t) => t.path === action.highlight.path);
    if (action.open || !tab) await openPath(action.highlight.path);
    else setTabState((s) => openTab(s, tab));
  };

  const dismissLinks = (doc: string): void => {
    handledLinks.current = markHandled(doc, linkAlerts[doc] ?? [], handledLinks.current);
    setLinkAlerts((m) => {
      const next = { ...m };
      delete next[doc];
      return next;
    });
  };

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

  const saveHwp = async (path: string): Promise<boolean> => {
    // 같은 문서의 저장은 한 번에 하나만: 늦게 끝난 옛 저장이 최신 내용을 덮지 않게 한다.
    if (!beginSave(path)) return false;
    setSavingPaths((m) => ({ ...m, [path]: true }));
    setSaveMessage("저장 중…");
    try {
      const exported = await exportHwp(path);
      const { bytes } = exported;
      const res = await fetch(`/api/hwp?path=${encodeURIComponent(path)}`, {
        method: "PUT",
        headers: { "content-type": "application/octet-stream" },
        body: bytes as BodyInit,
      });
      if (!res.ok) throw new Error("save");
      await notifyHwpSaved({ path, ...exported });
      setSaveMessage("저장했어요. 작업 폴더 파일에 반영됐어요.");
      return true;
    } catch {
      setSaveMessage("저장하지 못했어요.");
      return false;
    } finally {
      endSave(path);
      setSavingPaths((m) => ({ ...m, [path]: false }));
    }
  };

  const save = async (path: string): Promise<SaveResult> => {
    if (isHwpPath(path)) return (await saveHwp(path)) ? "saved" : "failed";
    setSaveMessage("저장 중…");
    try {
      // 방금 입력한 내용이 문서 서버에 닿을 때까지 잠깐 기다린다 (닿지 않은 편집은 강제 저장에 빠진다).
      const synced = await waitOfficeSynced(path, 15_000);
      // 서버는 문서 서버가 파일을 다 쓴 뒤에 응답한다.
      const res = await fetch(`/api/save?path=${encodeURIComponent(path)}`, { method: "POST" });
      const body = (await res.json()) as { error?: string };
      if (res.ok && !synced) {
        setSaveMessage("저장했지만 최근 편집이 문서 서버에 다 닿지 않아 파일에 빠졌을 수 있어요. 잠시 뒤 다시 저장해 주세요.");
      } else {
        setSaveMessage(res.ok ? "저장했어요. 작업 폴더 파일에 반영됐어요." : (body.error ?? "저장하지 못했어요."));
      }
      if (res.ok) return "saved";
      return res.status === 409 ? "no-editor" : "failed";
    } catch {
      setSaveMessage("저장하지 못했어요.");
      return "failed";
    }
  };

  // 원본의 최신 값으로 문서 숫자를 고친다. 열린 편집기의 편집을 먼저 파일에 저장해 잃지 않게 한다.
  const refreshLinks = async (doc: string): Promise<void> => {
    if (refreshingRef.current) return; // 한 번에 하나만: 같은 문서를 겹쳐 고치지 않는다.
    refreshingRef.current = true;
    setRefreshing(doc);
    // 고치는 동안 편집기 입력을 막는다: 저장 뒤에 친 글자가 편집기 교체로 사라지지 않게 한다.
    const focused = document.activeElement;
    if (focused instanceof HTMLElement) focused.blur();
    try {
      if (/\.(xlsx|pptx|docx)$/i.test(doc)) {
        // 편집이 문서 서버에 다 닿았다고 확인되고 저장도 끝나야 숫자를 고친다 (편집기 교체로 편집이 사라지지 않게).
        const synced = await waitOfficeSynced(doc, 15_000);
        if (!synced || (await save(doc)) === "failed") {
          setLinkResult({ doc, text: "열린 문서를 먼저 저장하지 못해 숫자를 고치지 않았어요.", lines: [] });
          return;
        }
      }
      const res = await fetch("/api/links/refresh", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path: doc }),
      });
      const body = (await res.json()) as {
        summary?: string;
        changes?: LinkChange[];
        warnings?: string[];
        error?: string;
      };
      if (!res.ok || body.error) {
        setLinkResult({ doc, text: body.error ?? "새 숫자로 고치지 못했어요.", lines: [] });
        return;
      }
      const changes = body.changes ?? [];
      dismissLinks(doc);
      if (changes.length > 0) reloadTab(doc);
      const lines = [...changes.map(changeLine), ...(body.warnings ?? [])];
      setLinkResult({ doc, text: body.summary ?? "", lines });
    } catch {
      setLinkResult({ doc, text: "새 숫자로 고치지 못했어요.", lines: [] });
    } finally {
      refreshingRef.current = false;
      setRefreshing(null);
    }
  };

  // 편집기에 방금 한 편집이 파일에 닿도록 먼저 저장한다. 저장하지 못했으면 false.
  const saveBefore = async (path: string): Promise<boolean> => {
    if (isHwpPath(path)) return saveHwp(path);
    if (!/\.(xlsx|pptx|docx)$/i.test(path)) return true;
    const synced = await waitOfficeSynced(path, 15_000);
    return synced && (await save(path)) !== "failed";
  };

  // 엑셀 탭에서 끌어 온 셀 범위를 놓은 탭의 문서에 표로 넣는다 (docmill 도구 insert_range).
  const dropRange = async (target: string): Promise<void> => {
    const from = dragged.current;
    dragged.current = null;
    setDropTarget(null);
    if (!from || droppingRef.current) return;
    const refusal = canDropOn(target);
    if (refusal) {
      setDropMessage(refusal);
      return;
    }
    droppingRef.current = true;
    let keepLocked = false; // 끊긴 삽입을 정리하지 못하면 편집기를 잠근 채 둔다
    setInsertingInto(target); // 넣는 동안 대상 편집기 입력을 막는다
    setDropMessage(`${baseName(from.source)}의 ${from.range}를 ${baseName(target)}에 넣는 중…`);
    try {
      if (!(await saveBefore(from.source)) || !(await saveBefore(target))) {
        setDropMessage("열린 문서를 먼저 저장하지 못해 범위를 넣지 않았어요.");
        return;
      }
      const res = await fetch("/api/insert-range", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ target, source: from.source, range: from.range, sheet: from.sheet }),
      });
      const body = (await res.json()) as { summary?: string; error?: string; locked?: boolean };
      if (!res.ok || body.error) {
        keepLocked = body.locked === true;
        setDropMessage(body.error ?? "범위를 문서에 넣지 못했어요.");
        return;
      }
      if (hasOfficePending(target)) {
        // 넣는 사이 입력한 편집이 사라지지 않게 다시 불러오지 않는다.
        setDropMessage(
          `${baseName(target)}: ${body.summary ?? "범위를 넣었어요."} 넣는 동안 입력한 편집이 있어 화면은 그대로예요. 화면의 편집은 파일에 저장되지 않았으니, 탭을 닫았다 다시 열면 넣은 표가 보이는 대신 그 편집은 사라져요.`,
        );
        return;
      }
      reloadTab(target);
      setDropMessage(`${baseName(target)}: ${body.summary ?? "범위를 넣었어요."}`);
    } catch {
      setDropMessage("범위를 문서에 넣지 못했어요.");
    } finally {
      droppingRef.current = false;
      if (!keepLocked) setInsertingInto(null);
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
                data-reloaded={reloads[t.path] ?? 0}
                data-drop-target={dropTarget === t.path ? "true" : undefined}
                onClick={() => setTabState((s) => openTab(s, t))}
                onDragOver={(e) => {
                  if (!dragged.current) return;
                  e.preventDefault();
                  e.stopPropagation();
                  setDropTarget(t.path);
                }}
                onDragLeave={() => setDropTarget((d) => (d === t.path ? null : d))}
                onDrop={(e) => {
                  if (!dragged.current) return;
                  e.preventDefault();
                  e.stopPropagation();
                  void dropRange(t.path);
                }}
              >
                {t.name}
              </button>
              <button
                className="close"
                aria-label={`탭 닫기: ${t.name}`}
                onClick={() => {
                  setTabState((s) => closeTab(s, t.path));
                  // 끊긴 삽입 때문에 잠근 편집기는 탭을 닫으면 풀린다 (다시 열 때 서버가 정리한다).
                  setInsertingInto((cur) => (cur === t.path && !droppingRef.current ? null : cur));
                }}
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

      {Object.entries(linkAlerts).map(([doc, sources]) => (
        <section key={doc} role="region" aria-label={`원본 변경 알림: ${baseName(doc)}`} className="alert">
          <span>
            {baseName(doc)}: 원본이 바뀌었어요 ({sources.map((s) => baseName(s.path)).join(", ")}).
            새 숫자로 고칠까요?
          </span>
          <button type="button" disabled={refreshing !== null} onClick={() => void refreshLinks(doc)}>
            새 숫자로 고치기
          </button>
          <button type="button" disabled={refreshing === doc} onClick={() => dismissLinks(doc)}>
            나중에
          </button>
        </section>
      ))}

      {dropMessage && (
        <section role="status" aria-label="범위 넣기 결과" className="alert">
          <p>{dropMessage}</p>
          <button type="button" onClick={() => setDropMessage(null)}>
            닫기
          </button>
        </section>
      )}

      {linkResult && (
        <section role="status" aria-label="새 숫자로 고친 결과" className="alert">
          <p>
            {baseName(linkResult.doc)}: {linkResult.text}
          </p>
          {linkResult.lines.length > 0 && (
            <ul>
              {linkResult.lines.map((line, i) => (
                <li key={i}>{line}</li>
              ))}
            </ul>
          )}
          <button type="button" onClick={() => setLinkResult(null)}>
            닫기
          </button>
        </section>
      )}

      {active && sentenceRefs.length > 0 && (
        <section role="region" aria-label="문장 출처" className="sentences">
          <h2>문장 출처</h2>
          <p className="hint">문장을 누르면 근거가 된 원본 위치를 원본 탭에서 보여 줘요.</p>
          <ul>
            {sentenceRefs.map((s, i) => (
              <li key={`${i}:${s.text}`}>
                <button type="button" title={`근거 위치: ${s.label}`} onClick={() => void showSource(s)}>
                  {s.text}
                </button>
              </li>
            ))}
          </ul>
          {sentenceMessage && <p role="status">{sentenceMessage}</p>}
        </section>
      )}

      {showExport && active && (
        <ExportDialog
          key={active.path}
          path={active.path}
          saveBefore={saveBefore}
          onClose={() => setShowExport(false)}
        />
      )}

      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} />}

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
          <button type="button" disabled={!activeEditing} onClick={() => setShowExport((v) => !v)}>
            내보내기
          </button>
          <button type="button" onClick={() => setShowSettings((v) => !v)}>
            설정
          </button>
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
          {active && highlight && highlight.path === active.path && (
            <div className="source-highlight-bar">
              <p data-role="source-highlight" role="status" className="source-highlight">
                근거 위치: {highlight.label} — “{highlight.text}”
              </p>
              <button type="button" onClick={() => setHighlight(null)}>
                강조 닫기
              </button>
            </div>
          )}
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
                {/\.xlsx$/i.test(active.path) && (
                  <span className="range-drag">
                    <label>
                      범위{" "}
                      <input
                        type="text"
                        value={rangeText}
                        placeholder="A1:C16"
                        onChange={(e) => setRangeText(e.target.value)}
                      />
                    </label>
                    <label>
                      시트{" "}
                      <input
                        type="text"
                        value={sheetText}
                        placeholder="첫 시트"
                        onChange={(e) => setSheetText(e.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      draggable={isCellRange(rangeText)}
                      title="PPT 탭으로 끌어다 놓으면 표로 들어가요"
                      onDragStart={(e) => {
                        dragged.current = { source: active.path, range: rangeText.trim(), sheet: sheetText };
                        e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(dragged.current));
                        e.dataTransfer.setData("text/plain", rangeText.trim());
                        e.dataTransfer.effectAllowed = "copy";
                      }}
                      onDragEnd={() => {
                        dragged.current = null;
                        setDropTarget(null);
                      }}
                    >
                      범위 끌기
                    </button>
                    <label>
                      넣을 PPT 탭{" "}
                      <select value={keyTarget} onChange={(e) => setKeyTarget(e.target.value)}>
                        <option value="">선택</option>
                        {tabState.tabs
                          .filter((t) => /\.pptx$/i.test(t.path))
                          .map((t) => (
                            <option key={t.path} value={t.path}>
                              {baseName(t.path)}
                            </option>
                          ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      disabled={!isCellRange(rangeText) || keyTarget === ""}
                      onClick={() => {
                        dragged.current = { source: active.path, range: rangeText.trim(), sheet: sheetText };
                        void dropRange(keyTarget);
                      }}
                    >
                      범위 넣기
                    </button>
                  </span>
                )}
              </div>
              {activeState === "unavailable" && (
                <p role="status" className="alert">
                  {NO_SERVER_NOTICE}. 문서 서버 주소(DOCMILL_ONLYOFFICE_URL)를 확인한 뒤 다시 열어 주세요.
                </p>
              )}
              {refreshing === active.path && (
                <div className="editor-lock" role="status">
                  새 숫자로 고치는 중이에요. 끝날 때까지 편집할 수 없어요.
                </div>
              )}
              <OfficeEditor
                key={`${active.path}#${reloads[active.path] ?? 0}`}
                path={active.path}
                locked={insertingInto === active.path || refreshing === active.path}
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
                key={`${t.path}#${reloads[t.path] ?? 0}`}
                path={t.path}
                active={t.path === tabState.active}
                onState={(st) => setEditorStates((m) => ({ ...m, [t.path]: st }))}
                onPages={(n) => setPageCounts((m) => ({ ...m, [t.path]: n }))}
              />
            ))}
        </main>
        {conflicts.map((p) => (
          <div key={p} role="alert" className="alert">
            <span>{p}에 저장하지 않은 편집이 있어 에이전트 결과를 아직 불러오지 않았어요.</span>
            <button type="button" onClick={() => reloadTab(p)}>
              편집을 버리고 다시 불러오기
            </button>
            <button type="button" onClick={() => setConflicts((c) => c.filter((x) => x !== p))}>
              내 편집 유지
            </button>
          </div>
        ))}
        <ChatPanel
          tabs={tabState.tabs.map((t) => t.path)}
          active={tabState.active}
          onPaths={onAgentPaths}
          onSave={save}
        />
      </div>
    </div>
  );
}
