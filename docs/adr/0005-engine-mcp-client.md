# 0005. 엔진은 내부 MCP 클라이언트로 호출한다

- 상태: 채택
- 날짜: 2026-11-12
- 관련: FR-02

## 배경
엔진(GridCraft·DeckCraft·WordCraft)은 `<엔진>-cli mcp`로 stdio MCP 서버를 제공한다.
docmill 도구는 한 작업 안에서 열기 → 명령 → 저장을 이어서 하므로 상태가 있는 세션이 필요하다.
MCP 클라이언트는 비동기(anyio)이고, `DocumentTool`은 동기 인터페이스다.

## 결정
- `anyio.from_thread.start_blocking_portal`로 전용 이벤트 루프 스레드를 두고, `stdio_client`·`ClientSession`을 그 안에서 연다. 어댑터는 동기 API와 컨텍스트 매니저를 제공한다. `anyio`를 직접 의존성에 추가한다(이미 `mcp`의 하위 의존성).
- 모든 호출(시작 포함)에 `anyio.fail_after(timeout)`(기본 60초)을 걸고, 초과하면 세션을 닫아 엔진 프로세스를 끝낸다.
- 열기·저장·내보내기 경로는 `engines/paths.py`가 `resolve()`(`..`·링크 해석) 후 작업 폴더 안인지 확인하고, 밖이면 엔진을 띄우기 전에 거부한다. Phase 0에서 엔진 옵션만으로는 저장이 막히지 않았기 때문이다.
- DeckCraft·WordCraft는 `--automation-read-root`·`--automation-write-root`를 작업 폴더로 지정해 실행한다. 작업 폴더가 없으면 생성자에서 `ValueError`.
- 엔진 경로는 `DOCMILL_GRIDCRAFT_PATH`·`DOCMILL_DECKCRAFT_PATH`·`DOCMILL_WORDCRAFT_PATH`, 없으면 PATH의 `*-cli`.
- 실패는 예외 대신 `EngineResult(ok=False, message=...)`로 돌려준다.

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| 호출마다 `asyncio.run` + 새 프로세스 | 단순 | 열기·저장 상태가 사라짐 | 세션 상태 필요 |
| 엔진 CLI `run --cmd` 직접 호출 | 비동기 불필요 | MCP 도구 계약(FR-02)과 다름 | 요구사항 위반 |

## 결과
- 어댑터는 스레드 하나와 프로세스 하나를 쓴다. 쓴 뒤 반드시 `close()`(또는 `with`)가 필요하다.
- 렌더링은 엔진마다 방식이 달라 어댑터에서 처리한다(GridCraft는 헤드리스에서 미지원, DeckCraft `export`, WordCraft `render_page` 이미지를 파일로 저장).
