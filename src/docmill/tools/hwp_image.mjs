// Inserts a picture in a new paragraph after a body paragraph of a hwp/hwpx file with @rhwp/core.
// Usage: node hwp_image.mjs <@rhwp/core dir> <input> <output .hwp> <image> <ext> <paragraph (1-based)>
//        <width HU> <height HU> <natural width px> <natural height px> <description>
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const [dir, input, output, image, ext, para, w, h, nw, nh, description = ""] = process.argv.slice(2);
try {
  const core = await import(pathToFileURL(join(dir, "rhwp.js")).href);
  core.initSync({ module: readFileSync(join(dir, "rhwp_bg.wasm")) });
  globalThis.measureTextWidth = () => 8;
  const doc = new core.HwpDocument(new Uint8Array(readFileSync(input)));
  const index = Number(para) - 1;
  const count = doc.getParagraphCount(0);
  if (!(index >= 0 && index < count)) {
    throw new Error(`문단 ${para}이(가) 없다 (1~${count})`);
  }
  const length = doc.getParagraphLength(0, index);
  const split = JSON.parse(doc.splitParagraph(0, index, length));
  if (!split.ok) throw new Error("문단을 나누지 못했다");
  const result = JSON.parse(
    doc.insertPicture(
      0, index + 1, 0, "", new Uint8Array(readFileSync(image)),
      Number(w), Number(h), Number(nw), Number(nh), ext, description,
    ),
  );
  if (!result.ok) throw new Error("그림을 넣지 못했다");
  writeFileSync(output, doc.exportHwp());
} catch (err) {
  process.stderr.write(String(err && err.message ? err.message : err));
  process.exit(1);
}
