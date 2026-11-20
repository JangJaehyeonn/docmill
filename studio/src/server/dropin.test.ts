import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createHash } from "node:crypto";
import {
  hasInsertJournal,
  isCellRange,
  LATE_SUMMARY,
  parseInsert,
  RECOVER_FAILED,
  runInsert,
  runRecover,
} from "./dropin.js";

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
  [ "$FAKE_RECOVER" = "fail" ] && exit 1
  if [ -f "$2.orig" ]; then cp "$2.orig" "$2"; echo '{"recovered": true, "state": "rolled_back"}'; exit 0; fi
  if [ "$FAKE_RECOVER" = "complete" ]; then echo '{"recovered": false, "state": "complete"}'; exit 0; fi
  echo '{"recovered": false, "state": "none"}'; exit 0
fi
doc="$2"
receipt=""
prev=""
for a in "$@"; do [ "$prev" = "--receipt" ] && receipt="$a"; prev="$a"; done
case "$FAKE_MODE" in
  args) printf '{"summary": "%s"}' "$(printf '%s|' "$@")" ;;
  full) echo new > "$doc"; echo new > "$doc.provenance.json"; echo '{"published": true}' > "$receipt"; sleep 5 ;;
  half) cp "$doc" "$doc.orig"; echo half > "$doc"; sleep 5 ;;
  other) echo other > "$doc"; echo other > "$doc.provenance.json"; sleep 5 ;;
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
    delete process.env.FAKE_RECOVER;
    await fs.rm(dir, { recursive: true, force: true });
  });
  const doc = () => path.join(dir, "a.pptx");

  it.skipIf(process.platform === "win32")("passes sheet names with edge spaces as they are", async () => {
    process.env.FAKE_MODE = "args";
    const r = await runInsert(doc(), path.join(dir, "s.xlsx"), "B2", dir, " Q3");
    expect(r.summary.endsWith("|--sheet| Q3|")).toBe(true);
    expect(r.summary).toContain("|--receipt|");
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

  it.skipIf(process.platform === "win32")("does not take another writer's change for this insert", async () => {
    process.env.FAKE_MODE = "other"; // e.g. refresh_links changed both files while this call waited
    const r = await runInsert(doc(), path.join(dir, "s.xlsx"), "B2", dir, "", 500);
    expect(r.error).toBeTruthy();
    expect(r.locked).toBeFalsy();
  });

  it.skipIf(process.platform === "win32")("trusts recovery that finds a completed publish", async () => {
    process.env.FAKE_MODE = "none";
    process.env.FAKE_RECOVER = "complete";
    const r = await runInsert(doc(), path.join(dir, "s.xlsx"), "B2", dir, "", 500);
    expect(r).toEqual({ summary: LATE_SUMMARY });
  });

  it.skipIf(process.platform === "win32")("keeps the editor locked when recovery fails", async () => {
    process.env.FAKE_MODE = "half";
    process.env.FAKE_RECOVER = "fail";
    const r = await runInsert(doc(), path.join(dir, "s.xlsx"), "B2", dir, "", 500);
    expect(r).toMatchObject({ error: RECOVER_FAILED, locked: true });
  });

  it.skipIf(process.platform === "win32")("finds the insert journal like the Python tool", async () => {
    const real = await fs.realpath(doc());
    const digest = createHash("sha256").update(real, "utf8").digest("hex").slice(0, 24);
    const journal = path.join(os.tmpdir(), `docmill-insert-${digest}`);
    expect(await hasInsertJournal(doc())).toBe(false);
    await fs.mkdir(journal, { recursive: true });
    try {
      expect(await hasInsertJournal(doc())).toBe(true);
      expect(await runRecover(doc(), dir)).toBe("none");
    } finally {
      await fs.rm(journal, { recursive: true, force: true });
    }
  });

  it.skipIf(process.platform === "win32")("reports failure when nothing was written", async () => {
    process.env.FAKE_MODE = "none";
    const r = await runInsert(doc(), path.join(dir, "s.xlsx"), "B2", dir, "", 500);
    expect(r.error).toBeTruthy();
  });
});
