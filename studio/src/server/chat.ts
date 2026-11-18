import { spawn } from "node:child_process";
import path from "node:path";

/** 에이전트가 이 시간 안에 끝나지 않으면 실패로 처리한다. */
export const AGENT_TIMEOUT_MS = 120_000;

/** 에이전트에 넘기지 않는 환경 변수 (구독 인증만 쓰고 API 키는 쓰지 않는다). */
const BLOCKED_ENV = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"];

export interface ChatRequest {
  message: string;
  tabs: string[];
  active: string | null;
  /** 개인정보를 가린 열린 문서 내용 (경로는 작업 폴더 기준 상대 경로). */
  documents?: { path: string; text: string }[];
}

/** 클라이언트로 흘려보내는 채팅 이벤트. */
export type ChatEvent =
  | { type: "tool"; id: string; name: string; input: unknown }
  | { type: "tool_result"; id: string; text: string; paths: string[] }
  | { type: "preview"; id: string; path: string; summary: string[] }
  | { type: "assistant"; text: string }
  | { type: "error"; message: string }
  | { type: "done" };

/** 에이전트 실행 환경: 현재 환경에서 API 키 계열을 뺀다. */
export function agentEnv(base: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {};
  for (const [k, v] of Object.entries(base)) {
    if (!BLOCKED_ENV.includes(k.toUpperCase())) env[k] = v;
  }
  return env;
}

/** 기본 에이전트 명령(claude CLI + docmill MCP 서버). */
export function defaultAgentCommand(): string {
  const mcp = JSON.stringify({ mcpServers: { docmill: { command: "docmill", args: ["mcp"] } } });
  const quoted = process.platform === "win32" ? `"${mcp.replace(/"/g, '\\"')}"` : `'${mcp}'`;
  return `claude -p --output-format stream-json --verbose --mcp-config ${quoted} --allowedTools "mcp__docmill__*"`;
}

/** 에이전트 stdin 으로 넘길 프롬프트: 열린 탭 목록·활성 파일(절대 경로)과 사용자 메시지. */
export function buildPrompt(workspace: string, req: ChatRequest): string {
  const abs = (p: string): string => path.join(workspace, ...p.split("/"));
  const lines = [
    `작업 폴더: ${workspace}`,
    `활성 파일: ${req.active ? abs(req.active) : "(없음)"}`,
    "열린 탭:",
    ...req.tabs.map((t) => `- ${path.basename(t)} → ${abs(t)}`),
    "사용자가 파일 이름으로 다른 탭을 가리키면 위 목록의 경로를 쓰고, 결과 파일은 새 파일로 저장하세요.",
    ...(req.documents ?? []).flatMap((d) => ["", `문서 내용 (${path.basename(d.path)}, 개인정보는 [주민번호1] 같은 자리표로 가림):`, d.text]),
    "",
    "사용자 메시지:",
    req.message,
  ];
  return lines.join("\n") + "\n";
}

/** 도구 결과 텍스트(JSON)에서 `path` 키의 문자열 값을 모은다. JSON 이 아니면 빈 목록. */
export function extractPaths(text: string): string[] {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return [];
  }
  const found: string[] = [];
  const walk = (v: unknown, depth: number): void => {
    if (depth > 6 || v === null || typeof v !== "object") return;
    if (Array.isArray(v)) {
      v.forEach((x) => walk(x, depth + 1));
      return;
    }
    for (const [k, x] of Object.entries(v)) {
      if (k === "path" && typeof x === "string") found.push(x);
      else walk(x, depth + 1);
    }
  };
  walk(data, 0);
  return found;
}

function resultText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((c: unknown) => {
      const text = (c as { text?: unknown } | null)?.text;
      return typeof text === "string" ? text : "";
    })
    .join("");
}

/** stream-json 한 줄을 채팅 이벤트 목록으로 바꾼다. 알 수 없는 줄은 무시한다. */
export function parseLine(line: string): ChatEvent[] {
  let obj: { type?: string; message?: { content?: unknown } };
  try {
    obj = JSON.parse(line) as typeof obj;
  } catch {
    return [];
  }
  const content = obj?.message?.content;
  if (!Array.isArray(content)) return [];
  const events: ChatEvent[] = [];
  for (const raw of content as Record<string, unknown>[]) {
    if (obj.type === "assistant" && raw.type === "tool_use") {
      events.push({ type: "tool", id: String(raw.id), name: String(raw.name), input: raw.input });
    } else if (obj.type === "assistant" && raw.type === "text" && typeof raw.text === "string") {
      events.push({ type: "assistant", text: raw.text });
    } else if (obj.type === "user" && raw.type === "tool_result") {
      const text = resultText(raw.content);
      events.push({ type: "tool_result", id: String(raw.tool_use_id), text, paths: extractPaths(text) });
    }
  }
  return events;
}

/**
 * 에이전트를 실행하고 이벤트를 `emit` 으로 보낸다. 실패·시간 초과도 error 이벤트로 알리고 예외는 던지지 않는다.
 * `signal` 이 중단되면 프로세스를 정리한다.
 */
export function runAgent(opts: {
  command: string;
  cwd: string;
  prompt: string;
  emit: (e: ChatEvent) => void;
  signal?: AbortSignal;
  timeoutMs?: number;
}): Promise<void> {
  const timeoutMs = opts.timeoutMs ?? AGENT_TIMEOUT_MS;
  return new Promise((resolve) => {
    const win = process.platform === "win32";
    const child = spawn(opts.command, {
      cwd: opts.cwd,
      env: agentEnv(process.env),
      shell: true,
      detached: !win,
      stdio: ["pipe", "pipe", "pipe"],
    });
    let finished = false;
    let stderr = "";
    let buffer = "";
    const kill = (): void => {
      try {
        if (win && child.pid) spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"]);
        else if (child.pid) process.kill(-child.pid, "SIGKILL");
      } catch {
        child.kill("SIGKILL");
      }
    };
    const finish = (error?: string): void => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      opts.signal?.removeEventListener("abort", onAbort);
      if (error) opts.emit({ type: "error", message: error });
      opts.emit({ type: "done" });
      resolve();
    };
    const timer = setTimeout(() => {
      kill();
      finish(`에이전트가 ${Math.round(timeoutMs / 1000)}초 안에 끝나지 않아 중단했어요 (시간 초과).`);
    }, timeoutMs);
    const onAbort = (): void => {
      kill();
      finish();
    };
    opts.signal?.addEventListener("abort", onAbort);
    child.stdout.on("data", (chunk: Buffer) => {
      buffer += chunk.toString("utf-8");
      let nl: number;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (line && !finished) parseLine(line).forEach(opts.emit);
      }
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr = (stderr + chunk.toString("utf-8")).slice(-2000);
    });
    child.stdin.on("error", () => undefined);
    child.on("error", (err) => finish(`에이전트를 실행하지 못했어요: ${err.message}`));
    child.on("close", (code) => {
      if (buffer.trim() && !finished) parseLine(buffer.trim()).forEach(opts.emit);
      if (code === 0) return finish();
      const detail = stderr.trim();
      finish(`에이전트가 오류로 끝났어요 (종료 코드 ${code ?? "알 수 없음"})${detail ? `: ${detail}` : ""}`);
    });
    child.stdin.end(opts.prompt);
  });
}
