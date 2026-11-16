import { promises as fs } from "node:fs";
import http from "node:http";
import path from "node:path";
import { addRecent, listRecent } from "./recent.js";
import { listFiles, resolveInWorkspace, WorkspaceError } from "./workspace.js";

export interface AppOptions {
  workspace: string;
  recentFile: string;
  clientDir: string;
}

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".json": "application/json; charset=utf-8",
};

function sendJson(res: http.ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(body));
}

async function readBody(req: http.IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf-8"));
  } catch {
    return {};
  }
}

async function handleApi(
  opts: AppOptions,
  req: http.IncomingMessage,
  res: http.ServerResponse,
  pathname: string,
): Promise<void> {
  if (req.method === "GET" && pathname === "/api/files") {
    return sendJson(res, 200, { files: await listFiles(opts.workspace) });
  }
  if (req.method === "GET" && pathname === "/api/recent") {
    const root = await fs.realpath(opts.workspace);
    const recent = await listRecent(opts.recentFile, root);
    const existing: string[] = [];
    for (const rel of recent) {
      try {
        existing.push(await resolveInWorkspace(root, rel));
      } catch {
        // 지워졌거나 열 수 없는 파일은 목록에서 뺀다.
      }
    }
    return sendJson(res, 200, { recent: existing });
  }
  if (req.method === "POST" && pathname === "/api/open") {
    const body = (await readBody(req)) as { path?: unknown };
    if (typeof body.path !== "string" || body.path === "") {
      return sendJson(res, 400, { error: "열 파일 경로가 없습니다." });
    }
    try {
      const rel = await resolveInWorkspace(opts.workspace, body.path);
      await addRecent(opts.recentFile, await fs.realpath(opts.workspace), rel);
      return sendJson(res, 200, { path: rel, name: path.posix.basename(rel) });
    } catch (err) {
      if (err instanceof WorkspaceError) return sendJson(res, 403, { error: err.message });
      throw err;
    }
  }
  sendJson(res, 404, { error: "알 수 없는 요청입니다." });
}

async function serveStatic(
  opts: AppOptions,
  res: http.ServerResponse,
  pathname: string,
): Promise<void> {
  const root = path.resolve(opts.clientDir);
  const target = path.resolve(root, "." + (pathname === "/" ? "/index.html" : pathname));
  const rel = path.relative(root, target);
  const inside = !rel.startsWith("..") && !path.isAbsolute(rel);
  const file = inside ? target : path.join(root, "index.html");
  try {
    const data = await fs.readFile(file);
    res.writeHead(200, { "content-type": MIME[path.extname(file)] ?? "application/octet-stream" });
    res.end(data);
  } catch {
    // 알 수 없는 경로는 앱 화면(index.html)으로 돌려보낸다.
    const data = await fs.readFile(path.join(root, "index.html"));
    res.writeHead(200, { "content-type": MIME[".html"] as string });
    res.end(data);
  }
}

/** studio HTTP 서버를 만든다 (listen 은 호출자가 한다). */
export function createApp(opts: AppOptions): http.Server {
  return http.createServer((req, res) => {
    const pathname = new URL(req.url ?? "/", "http://localhost").pathname;
    const task = pathname.startsWith("/api/")
      ? handleApi(opts, req, res, pathname)
      : serveStatic(opts, res, pathname);
    task.catch((err: unknown) => {
      console.error("요청 처리 실패:", err);
      if (!res.headersSent) sendJson(res, 500, { error: "서버 오류가 났습니다." });
      else res.end();
    });
  });
}
