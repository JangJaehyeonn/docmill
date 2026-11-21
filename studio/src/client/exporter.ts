/** 내보내기 종류. */
export type ExportKind = "pdf" | "folder" | "mail";

/** /api/export 응답. */
export interface ExportReply {
  path?: string;
  error?: string;
  needsConfirm?: boolean;
  failures?: string[];
}

/** 내보내기 결과를 사용자에게 보일 한국어 문장으로 바꾼다. 메일은 직접 보내지 않는다. */
export function exportMessage(kind: ExportKind, ok: boolean, reply: ExportReply): string {
  if (!ok || reply.error || !reply.path) return reply.error ?? "내보내지 못했어요.";
  if (kind === "mail") return `메일 초안을 만들었어요: ${reply.path} (메일은 보내지 않았어요)`;
  if (kind === "pdf") return `PDF로 내보냈어요: ${reply.path}`;
  return `폴더에 저장했어요: ${reply.path}`;
}
