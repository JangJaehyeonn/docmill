import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { isCellRange, LATE_SUMMARY, parseInsert, runInsert } from "./dropin.js";

describe("isCellRange", () => {
  it("accepts ranges and single cells", () => {
    expect(isCellRange("A1:C16")).toBe(true);
    expect(isCellRange(" b2 ")).toBe(true);
    expect(isCellRange("$A$1:$C$16")).toBe(true);
  });
  it("rejects other text", () => {
    for (const bad of ["", "A", "1:2", "A1:", "시트1!A1", "A1:C16; rm"]) expect(isCellRange(bad)).toBe(false);
  });
});

describe("parseInsert", () => {
  it("reads a success result", () => {
    const r = parseInsert(JSON.stringify({ summary: "넣었다", slide: 4, numbers: 15 }));
    expect(r).toEqual({ summary: "넣었다", slide: 4, numbers: 15 });
  });
  it("reports tool errors", () => {
    expect(parseInsert(JSON.stringify({ error: "실패", step: "range" })).error).toBe("실패");
  });
  it("reports unreadable output", () => {
    expect(parseInsert("not json").error).toBeTruthy();
    expect(parseInsert("{}").error).toBeTruthy();
    expect(parseInsert("null").error).toBeTruthy();
    expect(parseInsert("[]").error).toBeTruthy();
  });
});

describe("runInsert", () => {
  let dir = "";
  let oldPath = "";
  beforeEach(async () => {
    dir = await fs.mkdtemp(path.join(os.tmpdir(), "dropin-"));
    oldPath = process.env.PATH ?? "";
    // fake `docmill`: MODE picks what insert-range does; recover-insert restores doc.orig if present
    const script = `#!/bin/sh
if [ "$1" = "recover-insert" ]; then
  if [ -f "$2.orig" ] && [ ! -f "$2.provenance.json.new" ]; then cp "$2.orig" "$2"; echo '{"recovered": true}'; exit 0; fi
  echo '{"recovered": false}'; exit 0
fi
doc="$2"
case "$FAKE_MODE" in
  args) printf '{"summary": "%s"}' "$(printf '%s|' "$@")" ;;
  full) echo new > "$doc"; echo new > "$doc.provenance.json"; touch "$doc.provenance.json.new"; sleep 5 ;;
  half) cp "$doc" "$doc.orig"; echo half > "$doc"; sleep 5 ;;
  none) sleep 5 ;;
esac
`;
    await fs.writeFile(path.join(dir, "docmill"), script, { mode: 0o755 });
    process.env.PATH = `${dir}${path.delimiter}${oldPath}`;
    await fs.writeFile(path.join(dir, "a.pptx"), "old");
    await fs.writeFile(path.join(dir, "a.pptx.provenance.json"), "old");
  });
  afterEach(async () => {
    process.env.PATH = oldPath;
    delete process.env.FAKE_MODE;
    await fs.rm(dir, { recursive: true, force: true });
  });
  const doc = () => path.join(dir, "a.pptx");

  it.skipIf(process.platform === "win32")("passes sheet names with edge spaces as they are", async () => {
    process.env.FAKE_MODE = "args";
    const r = await runInsert(doc(), path.join(dir, "s.xlsx"), "B2", dir, " Q3");
    expect(r.summary.endsWith("|--sheet| Q3|")).toBe(true);
  });

  it.skipIf(process.platform === "win32")("reports a finished insert whose answer was lost as done", async () => {
    process.env.FAKE_MODE = "full";
    const r = await runInsert(doc(), path.join(dir, "s.xlsx"), "B2", dir, "", 500);
    expect(r).toEqual({ summary: LATE_SUMMARY });
  });

  it.skipIf(process.platform === "win32")("rolls back a half insert right away and reports failure", async () => {
    process.env.FAKE_MODE = "half";
    const r = await runInsert(doc(), path.join(dir, "s.xlsx"), "B2", dir, "", 500);
    expect(r.error).toBeTruthy();
    expect(await fs.readFile(doc(), "utf-8")).toBe("old");
  });

  it.skipIf(process.platform === "win32")("reports failure when nothing was written", async () => {
    process.env.FAKE_MODE = "none";
    const r = await runInsert(doc(), path.join(dir, "s.xlsx"), "B2", dir, "", 500);
    expect(r.error).toBeTruthy();
  });
});
