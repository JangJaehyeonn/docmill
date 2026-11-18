// Reads a hwp/hwpx file with @rhwp/core and prints JSON to stdout.
// Usage: node hwp_text.mjs <@rhwp/core directory> <hwp file> [lines|paragraphs]
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
  // getTextFileUnicode keeps characters CP949 cannot hold (getTextFileText turns them into &#N;).
  let text = doc.getTextFileUnicode();
  // The result is a JSON-encoded string.
  if (typeof text === "string" && text.startsWith('"')) text = JSON.parse(text);
  process.stdout.write(JSON.stringify(String(text).split(/\r\n|\r|\n/)));
} catch (err) {
  process.stderr.write(String(err && err.message ? err.message : err));
  process.exit(1);
}
