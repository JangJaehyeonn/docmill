import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";
import { rebaseDownloadUrl } from "./onlyoffice.js";
import type { PreflightCheck } from "./preflight.js";

/** 내보내기 종류: PDF 변환, 폴더에 저장, 메일 초안(.eml). */
export type ExportKind = "pdf" | "folder" | "mail";

/** 내보내기 결과: 만든 파일(작업 폴더 기준) 또는 실패 이유. */
export interface ExportResult {
  path?: string;
  error?: string;
}

/** 요청 본문의 종류 문자열을 확인한다. */
export function parseKind(value: unknown): ExportKind | null {
  return value === "pdf" || value === "folder" || value === "mail" ? value : null;
}

/** 점검 결과에서 확인을 받아야 하는 실패 항목만 고른다. */
export function failedChecks(checks: PreflightCheck[]): PreflightCheck[] {
  return checks.filter((c) => c.status === "실패");
}

/** 확인 창에 보일 실패 항목 한 줄. */
export function failureLine(c: PreflightCheck): string {
  return c.count > 0 ? `${c.name}: ${c.count}곳` : c.name;
}

/** `docmill export-doc` 로 폴더 저장·메일 초안을 만든다 (docmill 도구 export_document, AI 없음). */
export function runExportDoc(
  abs: string,
  kind: "folder" | "mail",
  workspace: string,
  folder = "",
  timeoutMs = 60_000,
): Promise<ExportResult> {
  const args = ["export-doc", abs, "--kind", kind, "--workspace", workspace];
  if (folder !== "") args.push("--folder", folder);
  return new Promise((resolve) => {
    execFile(
      "docmill",
      args,
      { timeout: timeoutMs, encoding: "utf-8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
      (err, stdout) => {
        const failed = { error: "내보내기를 실행하지 못했어요." };
        if (err && !stdout) return resolve(failed);
        try {
          const data = JSON.parse(stdout) as { path?: unknown; error?: unknown };
          if (typeof data.error === "string") return resolve({ error: data.error });
          resolve(typeof data.path === "string" && !err ? { path: data.path } : failed);
        } catch {
          resolve(failed);
        }
      },
    );
  });
}

/** PDF 파일이 비어 있지 않고 PDF 머리말로 시작하는지. */
export function looksLikePdf(data: Buffer): boolean {
  return data.length > 8 && data.subarray(0, 5).toString("latin1") === "%PDF-";
}

/** ONLYOFFICE 변환 서비스(ConvertService.ashx)로 문서를 PDF로 바꿔 문서 옆 `<이름>.pdf` 로 쓴다. 결과 path는 절대 경로. */
export async function convertToPdf(opts: {
  server: string;
  fileUrl: string;
  key: string;
  abs: string;
  timeoutMs?: number;
}): Promise<ExportResult> {
  const ext = path.extname(opts.abs).slice(1).toLowerCase();
  const signal = AbortSignal.timeout(opts.timeoutMs ?? 60_000);
  try {
    const res = await fetch(`${opts.server}/ConvertService.ashx`, {
      method: "POST",
      headers: { "content-type": "application/json", accept: "application/json" },
      body: JSON.stringify({
        async: false,
        filetype: ext,
        key: opts.key,
        outputtype: "pdf",
        title: path.basename(opts.abs),
        url: opts.fileUrl,
      }),
      signal,
    });
    const body = (await res.json()) as { fileUrl?: unknown; error?: unknown; endConvert?: unknown };
    if (typeof body.fileUrl !== "string" || body.endConvert === false) {
      return { error: "문서 서버가 PDF로 바꾸지 못했어요." };
    }
    const download = await fetch(rebaseDownloadUrl(body.fileUrl, opts.server), { signal });
    if (!download.ok) return { error: "PDF를 내려받지 못했어요." };
    const bytes = Buffer.from(await download.arrayBuffer());
    if (!looksLikePdf(bytes)) return { error: "문서 서버가 올바른 PDF를 주지 않았어요." };
    const target = opts.abs.slice(0, opts.abs.length - path.extname(opts.abs).length) + ".pdf";
    const tmp = `${target}.docmill-tmp`;
    await fs.writeFile(tmp, bytes);
    await fs.rename(tmp, target);
    return { path: target };
  } catch {
    return { error: "PDF로 바꾸지 못했어요. 문서 서버 연결을 확인해 주세요." };
  }
}
