const READY_PATTERN = /^READY (\S+)$/;

/** stdout 청크를 이어 붙이며 개행까지 받은 완성된 줄에서만 READY 주소를 찾는다. */
export class ReadyLineParser {
  private buffer = "";

  /** 청크를 넣고, 완성된 줄 중 `READY <url>` 이 있으면 그 url 을, 없으면 null 을 돌려준다. */
  push(chunk: string): string | null {
    this.buffer += chunk;
    let newline = this.buffer.indexOf("\n");
    while (newline !== -1) {
      const line = this.buffer.slice(0, newline).replace(/\r$/, "");
      this.buffer = this.buffer.slice(newline + 1);
      const match = READY_PATTERN.exec(line);
      if (match?.[1]) return match[1];
      newline = this.buffer.indexOf("\n");
    }
    return null;
  }
}
