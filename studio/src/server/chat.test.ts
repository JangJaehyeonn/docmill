import { describe, expect, it } from "vitest";
import { spawnSync } from "node:child_process";
import { agentEnv, buildPrompt, extractPaths, parseLine, runAgent, type ChatEvent } from "./chat";

describe("agentEnv", () => {
  it("API 키 계열 환경 변수를 뺀다", () => {
    const env = agentEnv({ ANTHROPIC_API_KEY: "sk", anthropic_auth_token: "t", PATH: "/bin" });
    expect(env).toEqual({ PATH: "/bin" });
  });
});

describe("buildPrompt", () => {
  it("활성 파일과 열린 탭을 절대 경로로 담는다", () => {
    const p = buildPrompt("/ws", { message: "안녕", tabs: ["a.xlsx", "d/b.docx"], active: "d/b.docx" });
    expect(p).toContain("활성 파일: /ws/d/b.docx");
    expect(p).toContain("/ws/a.xlsx");
    expect(p).toContain("안녕");
  });

  it("열린 모든 탭의 이름과 경로를 담는다", () => {
    const tabs = ["m.docx", "t.xlsx", "d/r.pptx"];
    const p = buildPrompt("/ws", { message: "보고서", tabs, active: "m.docx" });
    for (const t of tabs) {
      expect(p).toContain(`${t.split("/").pop()} → /ws/${t}`);
    }
  });
});

describe("extractPaths", () => {
  it("JSON 의 path 키를 모은다", () => {
    expect(extractPaths(JSON.stringify({ path: "/a.xlsx", sub: [{ path: "/b.docx" }] }))).toEqual([
      "/a.xlsx",
      "/b.docx",
    ]);
  });
  it("JSON 이 아니면 빈 목록", () => {
    expect(extractPaths("그냥 글")).toEqual([]);
  });
});

describe("parseLine", () => {
  it("도구 호출·결과·최종 메시지를 이벤트로 바꾼다", () => {
    const use = JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "tool_use", id: "t0", name: "x", input: {} }] },
    });
    const res = JSON.stringify({
      type: "user",
      message: { content: [{ type: "tool_result", tool_use_id: "t0", content: [{ type: "text", text: '{"path":"/a.xlsx"}' }] }] },
    });
    const text = JSON.stringify({ type: "assistant", message: { content: [{ type: "text", text: "끝" }] } });
    expect(parseLine(use)).toEqual([{ type: "tool", id: "t0", name: "x", input: {} }]);
    expect(parseLine(res)).toEqual([
      { type: "tool_result", id: "t0", text: '{"path":"/a.xlsx"}', paths: ["/a.xlsx"] },
    ]);
    expect(parseLine(text)).toEqual([{ type: "assistant", text: "끝" }]);
    expect(parseLine("깨진 줄")).toEqual([]);
  });
});

describe("runAgent", () => {
  const run = async (command: string, timeoutMs?: number): Promise<ChatEvent[]> => {
    const events: ChatEvent[] = [];
    await runAgent({ command, cwd: process.cwd(), prompt: "hi", emit: (e) => events.push(e), timeoutMs });
    return events;
  };

  it("0이 아닌 종료 코드는 error 이벤트로 알린다", async () => {
    const events = await run(`"${process.execPath}" -e "process.exit(3)"`);
    expect(events[0]).toMatchObject({ type: "error" });
    expect(events.at(-1)).toEqual({ type: "done" });
  });

  it("시간 초과면 error 이벤트로 알리고 프로세스를 끝낸다", async () => {
    const events = await run(`"${process.execPath}" -e "setTimeout(()=>{},60000)"`, 300);
    expect(events[0]).toMatchObject({ type: "error" });
    expect((events[0] as { message: string }).message).toContain("시간 초과");
  });

  // 가짜 에이전트: 자기 pid 를 assistant 이벤트로 알리고 오래 기다린다 (POSIX 는 exec 로 셸 없이 바로 node 가 된다).
  const node = JSON.stringify(process.execPath);
  const fake = (script: string): string =>
    `${process.platform === "win32" ? "" : "exec "}${node} -e ${JSON.stringify(script)}`;
  const announcePid =
    "console.log(JSON.stringify({type:'assistant',message:{content:[{type:'text',text:String(process.pid)}]}}));";
  const alive = (pid: number): boolean => {
    try {
      process.kill(pid, 0);
      return true;
    } catch {
      return false;
    }
  };
  const pidOf = (events: ChatEvent[]): number =>
    Number((events.find((e) => e.type === "assistant") as { text: string } | undefined)?.text);

  it("시간 초과면 프로세스가 끝난 뒤에야 done 을 보내고 반환한다", async () => {
    const events: ChatEvent[] = [];
    await runAgent({
      command: fake(`${announcePid} setTimeout(()=>{},60000)`),
      cwd: process.cwd(),
      prompt: "hi",
      emit: (e) => events.push(e),
      timeoutMs: 1000,
    });
    const pid = pidOf(events);
    expect(pid).toBeGreaterThan(0);
    // 반환 시점에 에이전트가 이미 없어야 이후 복원이 에이전트의 저장과 겹치지 않는다.
    expect(alive(pid)).toBe(false);
    expect(events.map((e) => e.type)).toEqual(["assistant", "error", "done"]);
  });

  it("중단되면 프로세스가 끝난 뒤에야 반환하고, 반환 뒤 복원 단계가 이어진다", async () => {
    const events: ChatEvent[] = [];
    const abort = new AbortController();
    const order: string[] = [];
    await runAgent({
      command: fake(`${announcePid} setTimeout(()=>{},60000)`),
      cwd: process.cwd(),
      prompt: "hi",
      signal: abort.signal,
      emit: (e) => {
        events.push(e);
        if (e.type === "assistant") abort.abort();
      },
    }).then(() => {
      // app.ts 는 runAgent 가 반환한 뒤 붙잡아 둔 결과의 자리표를 복원한다.
      order.push(alive(pidOf(events)) ? "restore-while-running" : "restore-after-exit");
    });
    expect(order).toEqual(["restore-after-exit"]);
    expect(events.at(-1)).toEqual({ type: "done" });
  });

  it.skipIf(process.platform === "win32")("프로세스가 닫히지 않아도 상한 뒤에는 반환한다", async () => {
    // 다른 세션으로 떨어져 나간 손자 프로세스가 stdout 을 쥐고 있으면 close 가 오지 않는다.
    const orphan =
      "const c=require('child_process').spawn(process.execPath,['-e','setTimeout(()=>{},60000)'],{detached:true,stdio:['ignore','inherit','ignore']});" +
      "console.log(JSON.stringify({type:'assistant',message:{content:[{type:'text',text:String(c.pid)}]}}));setTimeout(()=>{},60000);";
    const events: ChatEvent[] = [];
    const started = Date.now();
    await runAgent({
      command: fake(orphan),
      cwd: process.cwd(),
      prompt: "hi",
      emit: (e) => events.push(e),
      timeoutMs: 500,
      killWaitMs: 500,
    });
    const elapsed = Date.now() - started;
    const pid = pidOf(events);
    if (pid > 0) spawnSync("kill", ["-9", String(pid)]);
    expect(elapsed).toBeGreaterThanOrEqual(1000);
    expect(events.at(-1)).toEqual({ type: "done" });
  });
});
