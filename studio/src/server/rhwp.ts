import { promises as fs } from "node:fs";
import http from "node:http";
import path from "node:path";

/** rhwp 편집기 빌드에서 쓰이는 파일 형식. */
const RHWP_MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".wasm": "application/wasm",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".otf": "font/otf",
};

/** 한글 문서 확장자인지. */
export function isHwpFile(rel: string): boolean {
  return /\.(hwp|hwpx)$/i.test(rel);
}

/** `/rhwp/…` 주소를 빌드 폴더 안의 실제 경로로 바꾼다. 폴더 밖이면 null. */
export function resolveRhwpPath(distDir: string, pathname: string): string | null {
  let rest: string;
  try {
    rest = decodeURIComponent(pathname.slice("/rhwp/".length));
  } catch {
    return null;
  }
  if (rest.includes("\0")) return null;
  const root = path.resolve(distDir);
  const target = path.resolve(root, rest === "" ? "index.html" : rest);
  const rel = path.relative(root, target);
  return !rel.startsWith("..") && !path.isAbsolute(rel) ? target : null;
}

function sendError(res: http.ServerResponse, status: number, message: string): void {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify({ error: message }));
}

/** RHWP_STUDIO_DIST 폴더를 `/rhwp/` 아래로 내보낸다. 외부 사이트는 쓰지 않는다. */
export async function serveRhwp(
  distDir: string | undefined,
  res: http.ServerResponse,
  pathname: string,
): Promise<void> {
  if (!distDir) {
    return sendError(res, 404, "rhwp 편집기 빌드가 없습니다 (RHWP_STUDIO_DIST).");
  }
  const target = resolveRhwpPath(distDir, pathname);
  if (!target) return sendError(res, 403, "허용되지 않는 경로입니다.");
  try {
    const root = await fs.realpath(distDir);
    const real = await fs.realpath(target);
    const rel = path.relative(root, real);
    if (rel.startsWith("..") || path.isAbsolute(rel)) {
      return sendError(res, 403, "허용되지 않는 경로입니다.");
    }
    const data = await fs.readFile(real);
    res.writeHead(200, {
      "content-type": RHWP_MIME[path.extname(real).toLowerCase()] ?? "application/octet-stream",
    });
    res.end(data);
  } catch {
    sendError(res, 404, "파일을 찾을 수 없습니다.");
  }
}
