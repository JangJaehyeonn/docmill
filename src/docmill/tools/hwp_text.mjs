// Reads the text of a hwp/hwpx file with @rhwp/core and prints a JSON array of lines to stdout.
// Usage: node hwp_text.mjs <@rhwp/core directory> <hwp file>
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const [dir, file] = process.argv.slice(2);
try {
  const core = await import(pathToFileURL(join(dir, "rhwp.js")).href);
  core.initSync({ module: readFileSync(join(dir, "rhwp_bg.wasm")) });
  // rhwp asks the host for text metrics while laying out; a fixed width is enough to read text.
  globalThis.measureTextWidth = () => 8;
  const doc = new core.HwpDocument(new Uint8Array(readFileSync(file)));
  // getTextFileUnicode keeps characters CP949 cannot hold (getTextFileText turns them into &#N;).
  let text = doc.getTextFileUnicode();
  // The result is a JSON-encoded string.
  if (typeof text === "string" && text.startsWith('"')) text = JSON.parse(text);
  process.stdout.write(JSON.stringify(String(text).split(/\r\n|\r|\n/)));
} catch (err) {
  process.stderr.write(String(err && err.message ? err.message : err));
  process.exit(1);
}
