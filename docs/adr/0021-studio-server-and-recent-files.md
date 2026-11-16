# 0021. studio 서버는 Node 내장 http, 번들은 esbuild, 최근 파일은 DOCMILL_HOME에 저장

- 상태: 채택
- 날짜: 2026-11-16
- 관련: FR-26, ADR 0020

## 배경
FR-26은 `node studio/out/server.js --port <포트>`가 화면을 HTTP로 내보내고 `READY <url>`을 출력해야 한다.
Electron 창은 이 서버를 여는 껍데기다. 최근 파일은 앱을 다시 띄워도 남아야 한다.

## 결정
- 서버는 웹 프레임워크 없이 `node:http`로 만든다. API는 `/api/files`, `/api/recent`, `/api/open` 세 개뿐이다.
- 화면은 Vite로 `out/client`에 빌드하고, 서버와 Electron 진입점은 esbuild로 `out/server.js`, `out/electron/main.cjs`로 묶는다.
- 작업 폴더 밖 경로는 `realpath` 기준으로 판단해 심볼릭 링크 우회까지 거부하고, 한국어 사유를 돌려준다.
- 최근 파일은 `$DOCMILL_HOME/studio/recent.json`(기본 `~/.docmill`)에 작업 폴더별로 저장한다.

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| Express/Fastify | 라우팅 편함 | 의존성 증가 | API가 세 개뿐 |
| 최근 파일을 브라우저 localStorage에 저장 | 서버 코드 없음 | 서버를 다시 띄우면 포트가 바뀌어 출처가 달라져 사라짐 | 재시작 후 유지 요구를 못 지킴 |

## 결과
- 좋아지는 점: 의존성이 적고 `out/server.js` 한 파일로 실행된다.
- 감수하는 점: 라우팅이 커지면 프레임워크 도입을 다시 검토한다.
