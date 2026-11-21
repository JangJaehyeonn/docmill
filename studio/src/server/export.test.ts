import { describe, expect, it } from "vitest";
import { failedChecks, failureLine, looksLikePdf, parseKind } from "./export.js";

describe("export helpers", () => {
  it("accepts only known kinds", () => {
    expect(parseKind("pdf")).toBe("pdf");
    expect(parseKind("mail")).toBe("mail");
    expect(parseKind("send")).toBeNull();
    expect(parseKind(undefined)).toBeNull();
  });

  it("keeps only failed checks", () => {
    const checks = [
      { name: "개인정보", status: "실패", count: 2 },
      { name: "날짜 요일", status: "주의", count: 1 },
      { name: "서식", status: "통과", count: 0 },
    ];
    expect(failedChecks(checks).map(failureLine)).toEqual(["개인정보: 2곳"]);
  });

  it("recognises PDF bytes", () => {
    expect(looksLikePdf(Buffer.from("%PDF-1.7\n..."))).toBe(true);
    expect(looksLikePdf(Buffer.from("<html>"))).toBe(false);
  });
});

describe("writePdf", () => {
  it("writes inside the workspace and refuses links pointing outside", async () => {
    const { promises: fs } = await import("node:fs");
    const os = await import("node:os");
    const path = await import("node:path");
    const { writePdf, pdfTargetOf } = await import("./export.js");
    const base = await fs.mkdtemp(path.join(os.tmpdir(), "docmill-pdf-"));
    const ws = path.join(base, "ws");
    const outside = path.join(base, "outside");
    await fs.mkdir(ws);
    await fs.mkdir(outside);
    const target = pdfTargetOf(path.join(ws, "report.pptx"));
    expect(target).toBe(path.join(ws, "report.pdf"));
    const bytes = Buffer.from("%PDF-1.7\nbody");
    expect(await writePdf(ws, target, bytes)).toEqual({ path: target });
    expect(await fs.readFile(target)).toEqual(bytes);
    expect((await fs.readdir(ws)).sort()).toEqual(["report.pdf"]);
    const victim = path.join(outside, "victim.pdf");
    await fs.writeFile(victim, "keep");
    const link = path.join(ws, "other.pdf");
    await fs.symlink(victim, link);
    expect((await writePdf(ws, link, bytes)).error).toBeDefined();
    expect(await fs.readFile(victim, "utf-8")).toBe("keep");
    await fs.rm(base, { recursive: true, force: true });
  });
});
