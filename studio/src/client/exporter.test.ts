import { describe, expect, it } from "vitest";
import { exportMessage } from "./exporter";

describe("exportMessage", () => {
  it("says a mail draft was made and nothing was sent", () => {
    const text = exportMessage("mail", true, { path: "a.eml" });
    expect(text).toContain("a.eml");
    expect(text).not.toContain("보냈");
  });
  it("reports the server error", () => {
    expect(exportMessage("pdf", false, { error: "문서 서버에 연결되지 않아요." })).toBe(
      "문서 서버에 연결되지 않아요.",
    );
  });
  it("describes pdf and folder results", () => {
    expect(exportMessage("pdf", true, { path: "a.pdf" })).toContain("a.pdf");
    expect(exportMessage("folder", true, { path: "내보내기/a.docx" })).toContain("내보내기/a.docx");
  });
});
