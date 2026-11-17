# 0023. 채팅 에이전트 연결 방식

- 상태: 채택
- 날짜: 2026-11-17
- 관련: FR-29, #96

## 배경
studio 오른쪽 채팅 패널의 메시지를 AI에게 넘겨 docmill 도구로 문서를 고치게 해야 한다.
구독 인증만 쓰고 Anthropic API 키는 쓰지 않아야 하며, 테스트에서는 가짜 에이전트로 바꿔 끼울 수 있어야 한다.

## 결정
studio 서버(`POST /api/chat`)가 `DOCMILL_STUDIO_AGENT_CMD`(기본 `claude -p --output-format stream-json --verbose --mcp-config ... --allowedTools "mcp__docmill__*"`, docmill MCP 서버 연결과 docmill 도구만 사전 허용 포함)를 자식 프로세스로 실행한다.
열린 탭·활성 파일의 절대 경로와 사용자 메시지를 stdin 프롬프트로 넘기고, stdout의 stream-json을 줄 단위 JSON(NDJSON)으로 화면에 흘려보낸다.
실행 환경에서는 `ANTHROPIC_API_KEY`·`ANTHROPIC_AUTH_TOKEN`을 뺀다. 120초 안에 끝나지 않거나 비정상 종료하면 프로세스 그룹을 정리하고 실패 이유를 채팅에 보여 준다.
도구 결과 JSON의 `path`가 작업 폴더 안 문서면 열린 탭은 다시 불러오고, 없으면 새 탭으로 연다.

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| Anthropic API 직접 호출 | 제어가 쉽다 | API 키 필요 | 구독 인증만 쓰는 원칙 위반 |
| WebSocket | 양방향 | 의존성·연결 관리 추가 | 한 턴 단방향 스트림이면 충분 |

## 결과
에이전트를 환경 변수로 바꿔 끼울 수 있어 테스트가 쉽다. 에이전트가 알려 주는 경로만 믿으므로 서버가 작업 폴더 안인지 다시 확인한다.
