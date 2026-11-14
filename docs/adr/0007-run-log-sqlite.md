# 0007. 실행 로그는 SQLite `tool_runs` 테이블, 서버에서 도구를 일괄 감싼다

- 상태: 채택
- 날짜: 2026-11-14
- 관련: FR-16

## 배경
모든 도구 호출을 실행 ID와 함께 기록해야 하고, 도구마다 로깅 코드를 넣으면 누락되기 쉽다.

## 결정
- 저장 위치는 `DOCMILL_HOME`(기본 `~/.docmill`) 아래 `docmill.db`의 `tool_runs` 테이블이다.
- 열: `run_id`(uuid4 hex), `tool`, `args_summary`, `result`, `status`, `duration_ms`, `ai_used`, `started_at`.
- `build_server()`가 `server.add_tool`을 감싸 등록되는 모든 도구에 `runlog.logged`를 적용한다. 실패해도 기록 후 예외를 그대로 던진다.
- 인자 요약은 키 이름이 비밀값을 암시하면 가리고, 값은 200자로 자르며, 컨테이너·바이트는 크기만 남긴다.
- 로그 기록 실패는 도구 호출을 깨지 않고 stderr로만 남긴다.
- MCP로 호출된 도구의 `ai_used`는 true다. 레시피 실행(FR-10)은 `logged(..., ai_used=False)`를 쓴다.
- 시각(`started_at`)과 실행 ID는 로그에만 들어가며 도구 결과에는 섞지 않는다.

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| 도구마다 직접 기록 | 세밀한 제어 | 누락 위험 | 모든 호출 기록 요구 |

## 결과
- 새 도구는 등록만 하면 자동으로 기록된다.
