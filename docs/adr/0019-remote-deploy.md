# 0019. 원격 모드 배포: 토큰 인증, 헬스 체크, 엔진 포함 이미지

- 상태: 채택
- 날짜: 2026-11-16
- 관련: FR-24, #84, ADR 0014, ADR 0015

## 배경
ADR 0015의 HTTP 모드에는 인증이 없어 서버에 올려 운영할 수 없었다.
운영하려면 인증, 엔진 준비 상태 확인, 엔진이 포함된 이미지, 실패 시 되돌리는 배포 절차가 필요하다.

## 결정
- `DOCMILL_HTTP_TOKEN`이 있으면 ASGI 미들웨어(`docmill.remote.BearerTokenMiddleware`)가
  `/healthz`를 뺀 모든 요청의 `Authorization: Bearer` 값을 상수 시간 비교로 확인하고, 틀리면 401을 돌려준다.
  토큰이 없으면 `--host`를 무시하고 127.0.0.1에만 바인딩한다.
- `/healthz`는 엔진 실행 파일을 찾을 수 있는지(`McpEngineTool.locate`, 엔진 실행과 같은 탐색 규칙)로
  준비 상태를 정한다. 요청마다 엔진 프로세스를 띄우지 않는다. 엔진 버전 확인은 이미지 빌드 때 `docmill setup`이 한다.
- `Dockerfile`은 `docmill setup`으로 고정 버전 엔진을 설치하고 PATH에 연결한다. 기본 명령은 `0.0.0.0:8000` HTTP 모드이고,
  데이터(`DOCMILL_HOME`)는 `/data` 볼륨에 둔다.
- `deploy/deploy.sh`는 `docker pull` → 기존 컨테이너 교체(`docker run`) → `curl`로 `/healthz` 확인을 하고,
  시간 안에 통과하지 않으면 이전 이미지로 다시 실행한 뒤 1로 끝난다. `deploy/compose.yaml`은 수동 운영용이다.
- 개발 의존성에 `httpx`를 추가한다. 인수 테스트의 원격 MCP 클라이언트와 Starlette `TestClient`가 쓴다(런타임 의존성 아님).

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| SDK의 OAuth 토큰 검증(`token_verifier`) | SDK 표준 | 인가 서버 설정 필요, 단일 토큰엔 과함 | 요구사항은 고정 Bearer 토큰 |
| 헬스 체크마다 엔진 버전 실행 | 실제 실행 확인 | 요청마다 프로세스 3개, 느림 | 빌드 때 확인으로 충분 |
| 배포 스크립트에서 `docker compose` 사용 | compose 파일과 일원화 | 이전 이미지 되돌리기가 번거로움 | `docker run`이 단순하고 테스트하기 쉬움 |
| Dockerfile에서 curl로 엔진 직접 설치 | CI 액션과 같은 방식 | 설치 로직 중복 | `docmill setup` 재사용 |

## 결과
- 토큰을 설정하면 역방향 프록시 없이도 외부에 노출할 수 있다. TLS는 여전히 앞단(프록시·로드 밸런서)에서 처리해야 한다.
- 컨테이너 이미지의 기본 명령이 stdio에서 HTTP 모드로 바뀐다. stdio로 쓰려면 `docker run -i <이미지> mcp`.
- 릴리스 승인 후 `deploy/deploy.sh`를 실행하는 워크플로 연결은 보호 경로라 사람 승인 PR로 따로 한다.
