// Electron preload: 고른 파일의 실제 경로를 화면에 알려 준다 (브라우저에는 없는 기능).
import { contextBridge, webUtils } from "electron";

contextBridge.exposeInMainWorld("docmillStudio", {
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
});
