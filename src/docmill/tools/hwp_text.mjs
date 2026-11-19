// Reads a hwp/hwpx file with @rhwp/core and prints JSON to stdout.
// Usage: node hwp_text.mjs <@rhwp/core directory> <hwp file> [lines|paragraphs|tables]
//   tables: array of {section, paragraph, table, row, col, text} for body table cells
//   lines (default): array of text lines in document order
//   paragraphs: array of {section, paragraph, style, styleEnglish, text} for body paragraphs
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const [dir, file, mode = "lines"] = process.argv.slice(2);
try {
  const core = await import(pathToFileURL(join(dir, "rhwp.js")).href);
  core.initSync({ module: readFileSync(join(dir, "rhwp_bg.wasm")) });
  // rhwp asks the host for text metrics while laying out; a fixed width is enough to read text.
  globalThis.measureTextWidth = () => 8;
  const doc = new core.HwpDocument(new Uint8Array(readFileSync(file)));
  if (mode === "paragraphs") {
    const english = new Map(JSON.parse(doc.getStyleList()).map((s) => [s.id, s.englishName ?? ""]));
    const out = [];
    for (let s = 0; s < doc.getSectionCount(); s++) {
      for (let p = 0; p < doc.getParagraphCount(s); p++) {
        const length = doc.getParagraphLength(s, p);
        const style = JSON.parse(doc.getStyleAt(s, p));
        out.push({
          section: s + 1,
          paragraph: p + 1,
          style: style.name ?? "",
          styleEnglish: english.get(style.id) ?? "",
          text: length > 0 ? doc.getTextRange(s, p, 0, length) : "",
        });
      }
    }
    process.stdout.write(JSON.stringify(out));
    process.exit(0);
  }
  if (mode === "tables") {
    // Body tables only: {section, paragraph, table, row, col, text} (all 1-based), in cell order.
    const out = [];
    for (let s = 0; s < doc.getSectionCount(); s++) {
      for (let p = 0; p < doc.getParagraphCount(s); p++) {
        const controls = JSON.parse(doc.getControlTextPositions(s, p)).length;
        let table = 0;
        for (let c = 0; c < controls; c++) {
          let dims;
          try {
            dims = JSON.parse(doc.getTableDimensions(s, p, c));
          } catch {
            continue; // not a table control
          }
          table++;
          for (let k = 0; k < dims.cellCount; k++) {
            const info = JSON.parse(doc.getCellInfo(s, p, c, k));
            const parts = [];
            for (let q = 0; q < doc.getCellParagraphCount(s, p, c, k); q++) {
              const length = doc.getCellParagraphLength(s, p, c, k, q);
              parts.push(length > 0 ? doc.getTextInCell(s, p, c, k, q, 0, length) : "");
            }
            out.push({
              section: s + 1,
              paragraph: p + 1,
              table,
              row: info.row + 1,
              col: info.col + 1,
              text: parts.join("\n"),
            });
          }
        }
      }
    }
    process.stdout.write(JSON.stringify(out));
    process.exit(0);
  }
  // getTextFileUnicode keeps characters CP949 cannot hold (getTextFileText turns them into &#N;).
  let text = doc.getTextFileUnicode();
  // The result is a JSON-encoded string.
  if (typeof text === "string" && text.startsWith('"')) text = JSON.parse(text);
  process.stdout.write(JSON.stringify(String(text).split(/\r\n|\r|\n/)));
} catch (err) {
  process.stderr.write(String(err && err.message ? err.message : err));
  process.exit(1);
}
