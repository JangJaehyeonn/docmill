import { useEffect, useState } from "react";

interface Loaded {
  department?: string;
  name?: string;
  title?: string;
  approval_line?: string[];
  default_template?: string;
  tone?: string;
}

/** 결재 라인 입력(`기안자 > 팀장 > 부장`)을 목록으로 나눈다. */
export function parseApprovalLine(text: string): string[] {
  return text
    .split(">")
    .map((s) => s.trim())
    .filter((s) => s !== "");
}

/** 설정 화면: 한 번 입력하면 작업 폴더 설정 파일에 저장한다 (FR-45). */
export function SettingsPanel({ onClose }: { onClose: () => void }) {
  const [department, setDepartment] = useState("");
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [line, setLine] = useState("");
  const [template, setTemplate] = useState("");
  const [tone, setTone] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let stopped = false;
    fetch("/api/settings")
      .then(async (r) => {
        if (!r.ok) throw new Error(`settings ${r.status}`);
        return (await r.json()) as Loaded;
      })
      .then((s) => {
        if (stopped) return;
        setDepartment(s.department ?? "");
        setName(s.name ?? "");
        setTitle(s.title ?? "");
        setLine((s.approval_line ?? []).join(" > "));
        setTemplate(s.default_template ?? "");
        setTone(s.tone ?? "");
        setReady(true);
      })
      .catch(() => {
        if (!stopped) setMessage("설정을 불러오지 못했어요.");
      });
    return () => {
      stopped = true;
    };
  }, []);

  const save = async (): Promise<void> => {
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          department,
          name,
          title,
          approval_line: parseApprovalLine(line),
          default_template: template,
          tone,
        }),
      });
      setMessage(res.ok ? "설정을 저장했어요." : "설정을 저장하지 못했어요.");
    } catch {
      setMessage("설정을 저장하지 못했어요.");
    }
  };

  // 저장된 값을 불러온 뒤에 입력칸을 보여, 빈 칸을 보고 덮어쓰는 일이 없게 한다.
  if (!ready) return message ? <p role="alert" className="alert">{message}</p> : null;

  return (
    <section role="region" aria-label="설정" className="alert settings">
      <label>
        부서 <input type="text" value={department} onChange={(e) => setDepartment(e.target.value)} />
      </label>
      <label>
        이름 <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        직함 <input type="text" value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        결재 라인{" "}
        <input
          type="text"
          value={line}
          placeholder="기안자 > 팀장 > 부장"
          onChange={(e) => setLine(e.target.value)}
        />
      </label>
      <label>
        기본 양식 <input type="text" value={template} onChange={(e) => setTemplate(e.target.value)} />
      </label>
      <label>
        문체 <input type="text" value={tone} onChange={(e) => setTone(e.target.value)} />
      </label>
      <button type="button" onClick={() => void save()}>
        저장
      </button>
      <button type="button" onClick={onClose}>
        닫기
      </button>
      {message && <span role="status">{message}</span>}
    </section>
  );
}
