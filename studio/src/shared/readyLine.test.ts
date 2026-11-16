import { describe, expect, it } from "vitest";
import { ReadyLineParser } from "./readyLine";

describe("ReadyLineParser", () => {
  it("finds the url in a complete line", () => {
    const parser = new ReadyLineParser();
    expect(parser.push("READY http://127.0.0.1:49152/\n")).toBe("http://127.0.0.1:49152/");
  });

  it("waits for the newline when the chunk ends in the middle of the url", () => {
    const parser = new ReadyLineParser();
    expect(parser.push("READY http://127.0.0.1:4915")).toBeNull();
    expect(parser.push("2/\n")).toBe("http://127.0.0.1:49152/");
  });

  it("ignores other lines and handles CRLF", () => {
    const parser = new ReadyLineParser();
    expect(parser.push("starting\nREA")).toBeNull();
    expect(parser.push("DY http://127.0.0.1:5199/\r\nnext\n")).toBe("http://127.0.0.1:5199/");
  });

  it("does not match a line without a url", () => {
    const parser = new ReadyLineParser();
    expect(parser.push("READY\nREADY \n")).toBeNull();
  });
});
