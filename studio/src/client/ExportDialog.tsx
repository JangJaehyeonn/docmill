import { useEffect, useRef, useState } from "react";
import { exportMessage, type ExportKind, type ExportReply } from "./exporter";

interface Props {
  path: string;
  /** 편집기의 편집을 파일에 먼저 저장한다. 저장하지 못했으면 false. */
  saveBefore: (path: string) => Promise<boolean>;
  onClose: () => void;
}

const KINDS: { kind: ExportKind; label: string }[] = [
  { kind: "pdf", label: "PDF" },
  { kind: "folder", label: "폴더에 저장" },
  { kind: "mail", label: "메일 초안" },
];

/** 내보내기 대화상자: PDF·폴더·메일 초안 중 하나를 고르고, 점검에 실패가 있으면 확인을 받는다 (FR-46). */
export function ExportDialog({ path, saveBefore, onClose }: Props) {
  const [kind, setKind] = useState<ExportKind | null>(null);
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const [failures, setFailures] = useState<string[] | null>(null);
  const [checked, setChecked] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const firstRadio = useRef<HTMLInputElement | null>(null);

  // 열릴 때 첫 라디오로 초점을 옮기고, 닫힐 때 연 버튼으로 되돌린다 (비모달이라 가두지 않는다).
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    firstRadio.current?.focus();
    return () => opener?.focus();
  }, []);

  const run = async (confirmed: boolean): Promise<void> => {
    if (!kind || busy) return;
    setBusy(true);
    setMessage(null);
    try {
      if (!(await saveBefore(path))) {
        setMessage("열린 문서를 먼저 저장하지 못해 내보내지 않았어요.");
        return;
      }
      const res = await fetch("/api/export", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path, kind, folder, confirmed, checked: confirmed ? checked : null }),
      });
      const reply = (await res.json()) as ExportReply;
      if (res.ok && reply.needsConfirm) {
        setFailures(reply.failures ?? []);
        setChecked(reply.checked ?? null);
        return;
      }
      setFailures(null);
      setMessage(exportMessage(kind, res.ok, reply));
    } catch {
      setMessage("내보내지 못했어요.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="dialog" aria-label="내보내기" className="alert">
      <h2>내보내기</h2>
      {failures === null ? (
        <>
          <fieldset disabled={busy}>
            <legend>내보낼 방식</legend>
            {KINDS.map((k, idx) => (
              <label key={k.kind}>
                <input
                  type="radio"
                  name="export-kind"
                  ref={idx === 0 ? firstRadio : undefined}
                  checked={kind === k.kind}
                  onChange={() => setKind(k.kind)}
                />
                {k.label}
              </label>
            ))}
            {kind === "folder" && (
              <label>
                저장할 폴더{" "}
                <input
                  type="text"
                  value={folder}
                  placeholder="내보내기"
                  onChange={(e) => setFolder(e.target.value)}
                />
              </label>
            )}
          </fieldset>
          <button type="button" disabled={kind === null || busy} onClick={() => void run(false)}>
            실행
          </button>
        </>
      ) : (
        <>
          <p>보내기 전 점검에서 실패한 항목이 있어요. 그래도 진행할까요?</p>
          <ul>
            {failures.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
          <button type="button" disabled={busy} onClick={() => void run(true)}>
            그래도 내보내기
          </button>
          <button type="button" disabled={busy} onClick={() => setFailures(null)}>
            취소
          </button>
        </>
      )}
      {message && <p role="status">{message}</p>}
      <button type="button" onClick={onClose}>
        닫기
      </button>
    </div>
  );
}
