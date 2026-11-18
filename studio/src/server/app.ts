import { promises as fs } from "node:fs";
import { randomBytes } from "node:crypto";
import http from "node:http";
import path from "node:path";
import {
  buildEditorConfig,
  signPath,
  verifyPathSig,
  documentKey,
  isOnlyofficeFile,
  normalizeUrl,
  rebaseDownloadUrl,
  replaceFile,
  shouldSave,
} from "./onlyoffice.js";
import {
  buildPrompt,
  defaultAgentCommand,
  runAgent,
  type ChatEvent,
  type ChatRequest,
} from "./chat.js";
import {
  applyPreview,
  cancelPreview,
  parsePreview,
  registerPreview,
  type PendingPreview,
} from "./preview.js";
import { linkStatus, runRefresh, sourcesInside } from "./links.js";
import { addRecent, listRecent } from "./recent.js";
import { runPreflight } from "./preflight.js";
import { isHwpFile, serveRhwp } from "./rhwp.js";
import { fetchSuggestions } from "./suggest.js";
import { listFiles, resolveInWorkspace, WorkspaceError } from "./workspace.js";

export interface AppOptions {
  workspace: string;
  recentFile: string;
  clientDir: string;
  /** 문서 서버(ONLYOFFICE) 주소. 없으면 연결 안내를 보여준다. */
  onlyofficeUrl?: string;
  /** 문서 서버가 studio 서버에 접근할 때 쓰는 호스트. */
  publicHost?: string;
  /** /onlyoffice/* 주소 서명용 비밀값. 없으면 프로세스마다 새로 만든다. */
  secret?: string;
  /** 채팅 에이전트 명령(DOCMILL_STUDIO_AGENT_CMD). 없으면 claude CLI. */
  agentCmd?: string;
  /** rhwp 편집기 빌드 폴더(RHWP_STUDIO_DIST). `/rhwp/` 아래로 내보낸다. */
  rhwpDist?: string;
  /** 강제 저장 뒤 문서 서버 콜백이 파일을 쓸 때까지 기다리는 시간(ms). 기본 30초. */
  saveWaitMs?: number;
  /** 강제 저장이 error 1(편집 세션 없음)을 돌려줄 때 다시 시도하는 시간(초). 기본 5초. */
  forceSaveRetryOneSec?: number;
}

/** 한글 파일 저장 요청 본문의 최대 크기 (256MB). */
const MAX_HWP_BYTES = 256 * 1024 * 1024;

/** 편집기 세션의 문서 키 (강제 저장 요청에 쓴다). 경로별로 가장 최근 키를 기억한다. */
const sessionKeys = new Map<string, string>();
const fileLocks = new Map<string, Promise<void>>();

/** 같은 문서에 대한 저장 반영과 숫자 갱신을 한 번에 하나씩 차례로 실행한다. */
async function withFileLock<T>(rel: string, fn: () => Promise<T>): Promise<T> {
  const prev = fileLocks.get(rel) ?? Promise.resolve();
  let release!: () => void;
  const mine = new Promise<void>((r) => (release = r));
  const tail = prev.then(() => mine);
  fileLocks.set(rel, tail);
  await prev;
  try {
    return await fn();
  } finally {
    release();
    if (fileLocks.get(rel) === tail) fileLocks.delete(rel);
  }
}

/**
 * 강제 저장 요청별 대기자 (요청 토큰 → 결과 알림). 토큰은 forcesave 명령의 userdata로 보내고,
 * 문서 서버가 그 저장의 콜백에 userdata로 돌려준다. 문서 서버는 명령을 접수만 하고 파일은 나중에
 * 콜백으로 보내므로, 저장 응답을 "이 요청의" 콜백 처리 뒤로 미뤄야 저장 직후 디스크 파일을 읽는 쪽
 * (보내기 전 점검 등)이 최신 내용을 본다. 앞선 저장의 콜백은 뒤 요청을 깨우지 않는다.
 */
const saveWaiters = new Map<string, (ok: boolean) => void>();

interface SaveWait {
  token: string;
  promise: Promise<boolean | null>;
  /** 명령이 접수된 뒤 호출해 콜백 대기 시간을 잰다. 시간이 지나면 promise가 null로 끝난다. */
  start: (ms: number) => void;
  cancel: () => void;
}

/** 강제 저장 한 번의 콜백 결과를 기다린다 (명령을 보내기 전에 만들어 빠른 콜백도 놓치지 않는다). */
function waitForSaved(): SaveWait {
  const token = randomBytes(12).toString("hex");
  let timer: NodeJS.Timeout | undefined;
  let done: (ok: boolean | null) => void = () => undefined;
  const promise = new Promise<boolean | null>((resolve) => {
    done = resolve;
  });
  const finish = (ok: boolean | null): void => {
    if (timer) clearTimeout(timer);
    saveWaiters.delete(token);
    done(ok);
  };
  saveWaiters.set(token, (ok) => finish(ok));
  return {
    token,
    promise,
    start: (ms) => {
      if (saveWaiters.has(token)) timer = setTimeout(() => finish(null), ms);
    },
    cancel: () => finish(null),
  };
}

/** 저장 콜백 처리 결과를 그 요청을 기다리던 쪽에만 알린다. */
function notifySaveWaiter(token: unknown, ok: boolean): void {
  if (typeof token === "string") saveWaiters.get(token)?.(ok);
}

/** 적용·취소를 기다리는 미리보기 (ID → 원본·사본). */
const pendingPreviews = new Map<string, PendingPreview>();

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
  query: URLSearchParams,
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
  if (req.method === "GET" && pathname === "/api/onlyoffice") {
    return sendJson(res, 200, { url: normalizeUrl(opts.onlyofficeUrl) });
  }
  if (req.method === "GET" && pathname === "/api/editor-config") {
    return editorConfig(opts, req, res, query);
  }
  if (req.method === "GET" && pathname === "/api/suggest") {
    const file = await resolveQuery(opts, res, query);
    if (!file) return;
    return sendJson(res, 200, { suggestions: await fetchSuggestions(file.abs) });
  }
  if (req.method === "GET" && pathname === "/api/preflight") {
    const file = await resolveQuery(opts, res, query);
    if (!file) return;
    return sendJson(res, 200, await runPreflight(file.abs));
  }
  if (req.method === "GET" && pathname === "/api/links") {
    const file = await resolveQuery(opts, res, query);
    if (!file) return;
    const root = await fs.realpath(opts.workspace);
    return sendJson(res, 200, { sources: await linkStatus(root, file.abs) });
  }
  if (req.method === "POST" && pathname === "/api/links/refresh") return refreshLinks(opts, req, res);
  if (req.method === "POST" && pathname === "/api/chat") return chat(opts, req, res);
  if (req.method === "POST" && pathname === "/api/preview/apply") {
    return decide(opts, req, res, "apply");
  }
  if (req.method === "POST" && pathname === "/api/preview/cancel") {
    return decide(opts, req, res, "cancel");
  }
  if (pathname === "/api/hwp") return hwpFile(opts, req, res, query);
  if (req.method === "POST" && pathname === "/api/save") {
    return forceSave(opts, res, query);
  }
  sendJson(res, 404, { error: "알 수 없는 요청입니다." });
}

/** 미리보기 적용·취소. 서버가 만든 미리보기 ID 만 받는다. */
async function decide(
  opts: AppOptions,
  req: http.IncomingMessage,
  res: http.ServerResponse,
  action: "apply" | "cancel",
): Promise<void> {
  if (!String(req.headers["content-type"] ?? "").startsWith("application/json")) {
    return sendJson(res, 415, { error: "요청 형식이 맞지 않습니다." });
  }
  const body = (await readBody(req)) as { id?: unknown };
  const root = await fs.realpath(opts.workspace);
  const id = typeof body.id === "string" ? body.id : "";
  try {
    if (action === "apply") {
      const rel = await applyPreview(pendingPreviews, root, id);
      if (rel === null) return sendJson(res, 404, { error: "대기 중인 미리보기가 아닙니다." });
      sessionKeys.delete(rel);
      return sendJson(res, 200, { ok: true, path: rel });
    }
    if (!(await cancelPreview(pendingPreviews, root, id))) {
      return sendJson(res, 404, { error: "대기 중인 미리보기가 아닙니다." });
    }
    sendJson(res, 200, { ok: true });
  } catch (err) {
    console.error("미리보기 처리 실패:", err);
    sendJson(res, 500, { error: "미리보기를 처리하지 못했어요." });
  }
}

/** 연결된 원본의 최신 값으로 문서 숫자를 고친다 (docmill 도구 refresh_links, AI 없음). */
async function refreshLinks(
  opts: AppOptions,
  req: http.IncomingMessage,
  res: http.ServerResponse,
): Promise<void> {
  if (!String(req.headers["content-type"] ?? "").startsWith("application/json")) {
    return sendJson(res, 415, { error: "요청 형식이 맞지 않습니다." });
  }
  const body = (await readBody(req)) as { path?: unknown };
  const file = await resolveQuery(opts, res, new URLSearchParams({ path: String(body.path ?? "") }));
  if (!file) return;
  const root = await fs.realpath(opts.workspace);
  // CLI 가 읽을 원본이 모두 작업 폴더 안에 있을 때만 고친다.
  if (!(await sourcesInside(root, file.abs))) {
    return sendJson(res, 403, { error: "작업 폴더 밖이거나 없는 원본이 연결돼 있어 숫자를 고치지 않았어요." });
  }
  const result = await withFileLock(file.rel, async () => {
    const r = await runRefresh(file.abs, root);
    // 파일이 바뀌었으니 열린 편집 세션 키를 버려 다시 열 때 새 내용을 읽게 한다.
    if (!r.error && r.changes.length > 0) sessionKeys.delete(file.rel);
    return r;
  });
  sendJson(res, result.error ? 502 : 200, { path: file.rel, ...result });
}

/** 채팅 한 턴: 에이전트를 실행하고 이벤트를 줄 단위 JSON(NDJSON)으로 흘려보낸다. */
async function chat(
  opts: AppOptions,
  req: http.IncomingMessage,
  res: http.ServerResponse,
): Promise<void> {
  if (!String(req.headers["content-type"] ?? "").startsWith("application/json")) {
    return sendJson(res, 415, { error: "요청 형식이 맞지 않습니다." });
  }
  const body = (await readBody(req)) as Partial<ChatRequest>;
  if (typeof body.message !== "string" || body.message.trim() === "") {
    return sendJson(res, 400, { error: "보낼 메시지가 없습니다." });
  }
  const root = await fs.realpath(opts.workspace);
  const tabs: string[] = [];
  for (const t of Array.isArray(body.tabs) ? body.tabs : []) {
    try {
      tabs.push(await resolveInWorkspace(root, String(t)));
    } catch {
      // 작업 폴더 밖이거나 없는 탭은 에이전트에 알리지 않는다.
    }
  }
  let active: string | null = null;
  try {
    if (typeof body.active === "string") active = await resolveInWorkspace(root, body.active);
  } catch {
    active = null;
  }
  const prompt = buildPrompt(root, { message: body.message, tabs, active });
  res.writeHead(200, {
    "content-type": "application/x-ndjson; charset=utf-8",
    "cache-control": "no-store",
  });
  const abort = new AbortController();
  res.on("close", () => abort.abort());
  // 경로 확인이 비동기라 순서가 뒤바뀌지 않도록 이벤트를 한 줄로 이어서 보낸다.
  let queue: Promise<void> = Promise.resolve();
  const send = async (e: ChatEvent): Promise<void> => {
    const info = e.type === "tool_result" ? parsePreview(e.text) : null;
    const reg = info ? await registerPreview(pendingPreviews, root, info) : null;
    if (e.type === "tool_result" && info && reg) {
      // 사본에 적용된 결과: 원본은 건드리지 않고(경로 알림 없이) 결과 뒤에 미리보기 카드를 보낸다.
      if (!res.writableEnded) res.write(JSON.stringify({ ...e, paths: [] }) + "\n");
      e = { type: "preview", id: reg.id, path: reg.rel, summary: info.summary };
    } else if (e.type === "tool_result") {
      // 작업 폴더 안의 문서 파일만 상대 경로로 바꿔 알린다.
      const rel: string[] = [];
      for (const p of e.paths) {
        try {
          rel.push(await resolveInWorkspace(root, p));
        } catch {
          // 작업 폴더 밖·문서가 아닌 경로는 무시한다.
        }
      }
      // 파일이 바뀌었을 수 있으니 열린 편집 세션 키를 버려 다시 열 때 새 내용을 읽게 한다.
      for (const r of rel) sessionKeys.delete(r);
      e = { ...e, paths: rel };
    }
    if (!res.writableEnded) res.write(JSON.stringify(e) + "\n");
  };
  await runAgent({
    command: opts.agentCmd || defaultAgentCommand(),
    cwd: root,
    prompt,
    signal: abort.signal,
    emit: (e) => {
      queue = queue.then(() => send(e));
    },
  });
  await queue;
  res.end();
}

async function resolveQuery(
  opts: AppOptions,
  res: http.ServerResponse,
  query: URLSearchParams,
): Promise<{ rel: string; abs: string } | null> {
  try {
    const rel = await resolveInWorkspace(opts.workspace, query.get("path") ?? "");
    const abs = path.join(await fs.realpath(opts.workspace), ...rel.split("/"));
    return { rel, abs };
  } catch (err) {
    if (err instanceof WorkspaceError) sendJson(res, 403, { error: err.message });
    else throw err;
    return null;
  }
}

async function editorConfig(
  opts: AppOptions,
  req: http.IncomingMessage,
  res: http.ServerResponse,
  query: URLSearchParams,
): Promise<void> {
  const server = normalizeUrl(opts.onlyofficeUrl);
  if (!server) return sendJson(res, 404, { error: "문서 서버에 연결되지 않았어요." });
  const file = await resolveQuery(opts, res, query);
  if (!file) return;
  if (!isOnlyofficeFile(file.rel)) return sendJson(res, 400, { error: "지원하지 않는 형식입니다." });
  const info = await fs.stat(file.abs);
  // 열린 편집 세션이 있으면 같은 키를 쓴다 (강제 저장으로 파일이 바뀌어도 세션을 유지).
  const key = sessionKeys.get(file.rel) ?? documentKey(file.rel, info.mtimeMs, info.size);
  sessionKeys.set(file.rel, key);
  const port = req.socket.localPort ?? 0;
  const origin = `http://${opts.publicHost || "127.0.0.1"}:${port}`;
  const sig = signPath(secretOf(opts), file.rel);
  sendJson(res, 200, { server, config: buildEditorConfig(file.rel, key, origin, sig) });
}

async function readBytes(req: http.IncomingMessage): Promise<Buffer | null> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > MAX_HWP_BYTES) return null;
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks);
}

/** 한글 파일 읽기(GET)와 저장(PUT). 저장은 작업 폴더의 원본 파일을 통째로 바꾼다. */
async function hwpFile(
  opts: AppOptions,
  req: http.IncomingMessage,
  res: http.ServerResponse,
  query: URLSearchParams,
): Promise<void> {
  if (req.method !== "GET" && req.method !== "PUT") {
    return sendJson(res, 405, { error: "허용되지 않는 요청입니다." });
  }
  const file = await resolveQuery(opts, res, query);
  if (!file) return;
  if (!isHwpFile(file.rel)) return sendJson(res, 400, { error: "한글 파일(hwp, hwpx)이 아닙니다." });
  if (req.method === "GET") {
    res.writeHead(200, { "content-type": "application/octet-stream", "cache-control": "no-store" });
    res.end(await fs.readFile(file.abs));
    return;
  }
  // 다른 웹페이지가 몰래 보내는 저장 요청을 막기 위해 단순 요청 형식이 아닌 것만 받는다.
  if (req.headers["content-type"] !== "application/octet-stream") {
    return sendJson(res, 415, { error: "저장 형식이 맞지 않습니다." });
  }
  const data = await readBytes(req);
  if (!data) return sendJson(res, 413, { error: "파일이 너무 큽니다." });
  if (data.length === 0) return sendJson(res, 400, { error: "빈 파일은 저장할 수 없습니다." });
  await replaceFile(file.abs, data);
  sendJson(res, 200, { ok: true, bytes: data.length });
}

async function serveDocument(
  opts: AppOptions,
  res: http.ServerResponse,
  query: URLSearchParams,
): Promise<void> {
  const file = await resolveQuery(opts, res, query);
  if (!file) return;
  if (!verifyPathSig(secretOf(opts), file.rel, query.get("sig"))) {
    return sendJson(res, 403, { error: "서명이 없거나 맞지 않는 요청입니다." });
  }
  const data = await fs.readFile(file.abs);
  res.writeHead(200, { "content-type": "application/octet-stream" });
  res.end(data);
}

async function saveCallback(
  opts: AppOptions,
  req: http.IncomingMessage,
  res: http.ServerResponse,
  query: URLSearchParams,
): Promise<void> {
  const signed = await resolveQuery(opts, res, query);
  if (!signed) return;
  // 문서 서버에 준 콜백 주소(서명 포함)로 온 요청만 받는다.
  if (!verifyPathSig(secretOf(opts), signed.rel, query.get("sig"))) {
    return sendJson(res, 403, { error: "서명이 없거나 맞지 않는 요청입니다." });
  }
  const body = (await readBody(req)) as {
    status?: unknown;
    url?: unknown;
    key?: unknown;
    userdata?: unknown;
  };
  console.error("저장 콜백 수신: status=%s", String(body.status));
  const server = normalizeUrl(opts.onlyofficeUrl);
  if (body.status === 4 && typeof body.key === "string") {
    // 상태 4 = 변경 없이 닫힘: 일치하는 세션이면 키를 폐기해 다음에 열 때 새 키를 발급한다.
    const closed = await resolveQuery(opts, res, query);
    if (!closed) return;
    if (sessionKeys.get(closed.rel) === body.key) sessionKeys.delete(closed.rel);
  }
  if (shouldSave(body.status) && typeof body.url === "string" && server) {
    const file = await resolveQuery(opts, res, query);
    if (!file) return;
    // 열려 있는 편집 세션의 키와 일치하는 콜백만 받는다.
    if (typeof body.key !== "string" || sessionKeys.get(file.rel) !== body.key) {
      return sendJson(res, 403, { error: "편집 세션과 일치하지 않는 저장 요청입니다." });
    }
    try {
      const download = await fetch(rebaseDownloadUrl(body.url, server));
      if (!download.ok) throw new Error(`내려받기 실패 (${download.status})`);
      const bytes = Buffer.from(await download.arrayBuffer());
      const kept = await withFileLock(file.rel, async () => {
        // 내려받는 사이 갱신 등으로 세션이 끝났다면 이 저장은 버린다 (갱신 결과를 덮지 않는다).
        if (sessionKeys.get(file.rel) !== body.key) return false;
        await replaceFile(file.abs, bytes);
        // 상태 2 = 최종 저장: 세션이 끝났으니 다음에 열 때 새 키를 발급한다.
        if (body.status === 2) sessionKeys.delete(file.rel);
        return true;
      });
      notifySaveWaiter(body.userdata, kept);
    } catch (err) {
      console.error("저장 콜백 처리 실패:", err);
      notifySaveWaiter(body.userdata, false);
      return sendJson(res, 200, { error: 1 });
    }
  }
  sendJson(res, 200, { error: 0 });
}

async function commandForceSave(server: string, key: string, userdata: string): Promise<number> {
  const r = await fetch(`${server}/coauthoring/CommandService.ashx`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    // userdata는 이 저장의 콜백에 그대로 돌아와, 어떤 요청의 저장인지 구분하는 데 쓴다.
    body: JSON.stringify({ c: "forcesave", key, userdata }),
  });
  return ((await r.json()) as { error?: number }).error ?? -1;
}

async function forceSave(
  opts: AppOptions,
  res: http.ServerResponse,
  query: URLSearchParams,
): Promise<void> {
  const server = normalizeUrl(opts.onlyofficeUrl);
  const rel = query.get("path") ?? "";
  const key = sessionKeys.get(rel);
  if (!server || !key) return sendJson(res, 409, { error: "저장할 편집기가 열려 있지 않습니다." });
  // 명령을 보내기 전에 대기를 걸어 두어야 빠르게 오는 콜백도 놓치지 않는다.
  const saved = waitForSaved();
  try {
    // error 4 = 아직 반영할 변경이 없음. 편집 내용이 문서 서버에 닿을 시간을 주고 몇 번 다시 요청한다.
    // error 1 = 이 키의 편집 세션이 아직 문서 서버에 없음. 방금 한 편집이 아직 닿지 않았을 수도 있으므로
    // 잠깐(최대 retryOneSec초) 다시 요청하고, 그래도 1이면 편집이 없는 것으로 본다 (연 뒤 바로 점검하는 경우).
    const retryOneSec = opts.forceSaveRetryOneSec ?? 5;
    let code = await commandForceSave(server, key, saved.token);
    for (let i = 0; i < 30 && (code === 4 || (code === 1 && i < retryOneSec)); i++) {
      await new Promise((r) => setTimeout(r, 1000));
      code = await commandForceSave(server, key, saved.token);
    }
    const unchanged = code === 1 || code === 4;
    if (code !== 0 && !unchanged) throw new Error(`문서 서버 오류 ${code}`);
    if (unchanged) {
      saved.cancel();
      return sendJson(res, 200, { ok: true, unchanged });
    }
    // 접수됨(0): 대기 시간은 지금(접수 시점)부터 잰다. 이 요청의 콜백이 파일을 다 쓸 때까지 기다린다.
    saved.start(opts.saveWaitMs ?? 30_000);
    const ok = await saved.promise;
    if (ok === null) return sendJson(res, 504, { error: "문서 서버의 저장이 시간 안에 끝나지 않았습니다." });
    if (!ok) return sendJson(res, 502, { error: "문서 서버에서 받은 파일을 쓰지 못했습니다." });
    sendJson(res, 200, { ok: true, unchanged: false });
  } catch (err) {
    saved.cancel();
    console.error("저장 요청 실패:", err);
    sendJson(res, 502, { error: "문서 서버에 저장을 요청하지 못했습니다." });
  }
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
    res.end(path.extname(file) === ".html" ? await withBootFiles(opts, data) : data);
  } catch {
    // 알 수 없는 경로는 앱 화면(index.html)으로 돌려보낸다.
    const data = await fs.readFile(path.join(root, "index.html"));
    res.writeHead(200, { "content-type": MIME[".html"] as string });
    res.end(await withBootFiles(opts, data));
  }
}

/** 앱 화면 HTML 맨 앞에 작업 폴더 파일 목록을 넣어, 화면이 뜨자마자 목록을 보이게 한다. */
async function withBootFiles(opts: AppOptions, html: Buffer): Promise<Buffer> {
  let files: string[] = [];
  try {
    files = await listFiles(opts.workspace);
  } catch {
    // 목록을 못 읽으면 화면이 직접 조회해 오류를 보여 준다.
  }
  const json = JSON.stringify(files).replace(/</g, "\\u003c");
  const tag = `<script>window.__DOCMILL_FILES__=${json};</script>`;
  const text = html.toString("utf8");
  // doctype 앞에 넣으면 호환 모드가 되므로 <head> 안에 넣는다.
  const at = text.search(/<\/head>/i);
  return Buffer.from(at >= 0 ? text.slice(0, at) + tag + text.slice(at) : text + tag);
}

const secrets = new WeakMap<AppOptions, string>();

/** 이 앱의 서명 비밀값 (옵션에 없으면 처음 쓸 때 무작위로 만든다). */
function secretOf(opts: AppOptions): string {
  let s = opts.secret ?? secrets.get(opts);
  if (!s) {
    s = randomBytes(32).toString("hex");
    secrets.set(opts, s);
  }
  return s;
}

/** 요청이 같은 컴퓨터(루프백)에서 왔는지. */
export function isLoopback(address: string | undefined): boolean {
  return (
    address === "127.0.0.1" ||
    address === "::1" ||
    address === "::ffff:127.0.0.1" ||
    (address ?? "").startsWith("127.")
  );
}

/** studio HTTP 서버를 만든다 (listen 은 호출자가 한다). */
export function createApp(opts: AppOptions): http.Server {
  return http.createServer((req, res) => {
    let pathname: string;
    let query: URLSearchParams;
    try {
      const parsed = new URL(req.url ?? "/", "http://localhost");
      pathname = parsed.pathname;
      query = parsed.searchParams;
    } catch {
      sendJson(res, 400, { error: "잘못된 요청 주소입니다." });
      return;
    }
    // 앱 화면과 API는 같은 컴퓨터에서만 쓴다. 다른 호스트(예: Docker 문서 서버)는 서명된 /onlyoffice/* 만 부를 수 있다.
    if (!pathname.startsWith("/onlyoffice/") && !isLoopback(req.socket.remoteAddress)) {
      sendJson(res, 403, { error: "이 컴퓨터에서만 열 수 있습니다." });
      return;
    }
    let task: Promise<void>;
    if (pathname.startsWith("/api/")) task = handleApi(opts, req, res, pathname, query);
    else if (req.method === "GET" && pathname.startsWith("/rhwp/")) {
      task = serveRhwp(opts.rhwpDist, res, pathname);
    } else if (req.method === "GET" && pathname === "/onlyoffice/file") {
      task = serveDocument(opts, res, query);
    } else if (req.method === "POST" && pathname === "/onlyoffice/callback") {
      task = saveCallback(opts, req, res, query);
    } else task = serveStatic(opts, res, pathname);
    task.catch((err: unknown) => {
      console.error("요청 처리 실패:", err);
      if (!res.headersSent) sendJson(res, 500, { error: "서버 오류가 났습니다." });
      else res.end();
    });
  });
}
