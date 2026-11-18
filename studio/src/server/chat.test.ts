import { describe, expect, it } from "vitest";
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
});
