import { useCallback, useEffect, useRef, useState } from "react";
import { closeTab, openTab, type TabState } from "./tabs";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  return (await res.json()) as T;
}

function baseName(p: string): string {
  return p.slice(p.lastIndexOf("/") + 1);
}

/** studio 화면: 위쪽 탭, 왼쪽 작업 폴더 파일 목록, 가운데 시작 화면(최근 파일). */
export function App() {
  const [files, setFiles] = useState<string[]>([]);
  const [recent, setRecent] = useState<string[]>([]);
  const [tabState, setTabState] = useState<TabState>({ tabs: [], active: null });
  const [error, setError] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    const [f, r] = await Promise.all([
      getJson<{ files: string[] }>("/api/files"),
      getJson<{ recent: string[] }>("/api/recent"),
    ]);
    setFiles(f.files);
    setRecent(r.recent);
    return f.files;
  }, []);

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
      const file = { path: body.path, name: body.name };
      setTabState((s) => openTab(s, file));
      await refresh();
    },
    [refresh],
  );

  useEffect(() => {
    const target = new URLSearchParams(window.location.search).get("open");
    void refresh().then(() => {
      if (target) void openPath(target);
    });
  }, [refresh, openPath]);

  // 브라우저는 파일의 전체 경로를 알려 주지 않으므로 이름으로 작업 폴더의 파일을 찾는다.
  const openByName = (name: string): void => {
    const match = files.find((f) => baseName(f) === name);
    if (match) void openPath(match);
    else setError(`'${name}' 파일은 작업 폴더에 없습니다. 작업 폴더 안의 파일만 열 수 있습니다.`);
  };

  const onDrop = (e: React.DragEvent): void => {
    e.preventDefault();
    const dropped = e.dataTransfer.files[0];
    if (dropped) openByName(dropped.name);
  };

  const active = tabState.tabs.find((t) => t.path === tabState.active);

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
                aria-label={`${t.name} 닫기`}
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
        </div>
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
              if (picked) openByName(picked.name);
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

        <main className="main" role="tabpanel">
          {active ? (
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
        </main>
      </div>
    </div>
  );
}
