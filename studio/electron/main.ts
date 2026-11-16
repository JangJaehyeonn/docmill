// Electron 껍데기: studio 서버(out/server.js)를 띄우고 READY 주소를 창으로 연다.
import { spawn } from "node:child_process";
import path from "node:path";
import { app, BrowserWindow } from "electron";
import { ReadyLineParser } from "../src/shared/readyLine";

function startServer(): Promise<string> {
  const server = spawn(process.execPath, [path.join(__dirname, "..", "server.js")], {
    env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" },
    stdio: ["ignore", "pipe", "inherit"],
  });
  app.on("quit", () => server.kill());
  return new Promise((resolve, reject) => {
    const parser = new ReadyLineParser();
    server.stdout.setEncoding("utf-8");
    server.stdout.on("data", (chunk: string) => {
      const url = parser.push(chunk);
      if (url) resolve(url);
    });
    server.on("exit", () => reject(new Error("studio 서버가 먼저 종료되었습니다.")));
  });
}

void app.whenReady().then(async () => {
  const url = await startServer();
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    title: "docmill studio",
    // preload 는 파일 선택기·끌어다 놓기로 고른 파일의 실제 경로를 화면에 알려 준다.
    webPreferences: { preload: path.join(__dirname, "preload.cjs") },
  });
  await win.loadURL(url);
});

app.on("window-all-closed", () => app.quit());
