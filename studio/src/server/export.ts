import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
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

/** 문서 옆 PDF 경로 (`<이름>.pdf`, 절대 경로). */
export function pdfTargetOf(abs: string): string {
  return abs.slice(0, abs.length - path.extname(abs).length) + ".pdf";
}

/** 경로가 작업 폴더(실제 경로) 안인지. */
function insideRoot(root: string, abs: string): boolean {
  const rel = path.relative(root, abs);
  return rel !== "" && !rel.startsWith("..") && !path.isAbsolute(rel);
}

/** 링크를 따라간 실제 경로. 파일이 아직 없으면 폴더의 실제 경로에 이름을 붙인다. */
async function realTarget(abs: string): Promise<string> {
  try {
    return await fs.realpath(abs);
  } catch {
    return path.join(await fs.realpath(path.dirname(abs)), path.basename(abs));
  }
}

/**
 * PDF 바이트를 `target` 에 쓴다. 대상(링크 해석 포함)이 작업 폴더 밖이면 쓰지 않는다.
 * 요청마다 다른 임시 파일을 새로(`wx`) 만들어 쓴 뒤 바꿔 넣고, 실패하면 임시 파일을 지운다.
 * 호출하는 쪽은 같은 `target` 에 대한 쓰기를 잠금으로 차례로 실행해야 한다.
 */
export async function writePdf(root: string, target: string, bytes: Buffer): Promise<ExportResult> {
  const realRoot = await fs.realpath(root);
  if (!insideRoot(realRoot, await realTarget(target))) {
    return { error: "작업 폴더 밖의 파일에는 쓸 수 없어요." };
  }
  const tmp = `${target}.${randomUUID()}.docmill-tmp`;
  try {
    await fs.writeFile(tmp, bytes, { flag: "wx" });
    await fs.rename(tmp, target);
  } catch {
    await fs.rm(tmp, { force: true });
    return { error: "PDF 파일을 쓰지 못했어요." };
  }
  return { path: target };
}

/** ONLYOFFICE 변환 서비스(ConvertService.ashx)로 문서를 PDF로 바꿔 그 바이트를 돌려준다. */
export async function convertToPdf(opts: {
  server: string;
  fileUrl: string;
  key: string;
  abs: string;
  timeoutMs?: number;
}): Promise<{ bytes?: Buffer; error?: string }> {
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
    return { bytes };
  } catch {
    return { error: "PDF로 바꾸지 못했어요. 문서 서버 연결을 확인해 주세요." };
  }
}
