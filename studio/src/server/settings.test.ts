import { mkdtemp, readFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { buildPrompt } from "./chat.js";
import { normalizeSettings, readSettings, settingsLines, writeSettings } from "./settings.js";

describe("settings", () => {
  it("값을 다듬고 모르는 키는 버린다", () => {
    const s = normalizeSettings({ department: " 영업팀 ", approval_line: ["기안자", "", 3], extra: 1 });
    expect(s.department).toBe("영업팀");
    expect(s.approval_line).toEqual(["기안자"]);
    expect(s).not.toHaveProperty("extra");
  });

  it("저장한 설정을 다시 읽는다", async () => {
    const ws = await mkdtemp(path.join(os.tmpdir(), "settings-"));
    expect((await readSettings(ws)).department).toBe("");
    await writeSettings(ws, { department: "영업팀", approval_line: ["기안자", "팀장"] });
    expect((await readSettings(ws)).approval_line).toEqual(["기안자", "팀장"]);
    const file = JSON.parse(await readFile(path.join(ws, ".docmill", "settings.json"), "utf-8"));
    expect(file.department).toBe("영업팀");
  });

  it("에이전트 입력에 부서와 결재 라인이 들어간다", () => {
    const lines = settingsLines(normalizeSettings({ department: "영업팀", approval_line: ["기안자", "팀장"] }));
    const prompt = buildPrompt("/w", { message: "안녕", tabs: [], active: null, settingsLines: lines });
    expect(prompt).toContain("부서: 영업팀");
    expect(prompt).toContain("결재 라인: 기안자 > 팀장");
    expect(settingsLines(normalizeSettings({}))).toEqual([]);
  });
});
