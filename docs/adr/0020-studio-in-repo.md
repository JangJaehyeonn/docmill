# 0020. docmill studio를 같은 저장소의 `studio/`에서 만든다

- 상태: 채택
- 날짜: 2026-11-16
- 관련: FR-26 ~ FR-50

## 배경
문서 화면 옆에 AI 채팅이 붙은 데스크톱 앱(docmill studio)을 만든다. 엑셀·PPT·워드·한글 편집기를 직접 만들 수는 없으므로
오픈소스 편집기를 넣고, 문서를 바꾸는 기능은 이미 있는 docmill 도구를 쓴다.
0단계 기술 검증 결과:
- rhwp: npm `@rhwp/core` 0.9.0의 WASM으로 rhwp 저장소 v0.9.0의 rhwp-studio를 Rust 빌드 없이 빌드할 수 있다.
  `@rhwp/editor`로 넣어 hwp 열기, 명령 191개 조회, `exportBytes` 저장이 됐다. `file:` 출처는 거부한다.
- ONLYOFFICE Docs 8.3: GitHub Actions에서 Docker(host 네트워크, JWT 끔)로 띄워 xlsx를 열었고 2초 만에 문서 준비 완료, 저장 콜백을 받았다.
  문서 서버가 80·8000 포트를 쓴다.
- rhwp-studio에는 자체 AI 채팅 패널이 이미 있다. 한글 편집기 옆 채팅 자체는 차별점이 아니다.

## 결정
같은 저장소의 `studio/`(Node 22, TypeScript, React, Vite, Electron)에 만든다.
화면은 `node studio/out/server.js`가 HTTP로 내보내고, Electron은 그 서버를 여는 껍데기다.
인수 테스트는 이 서버를 Python Playwright로 연다. 문서를 바꾸는 기능은 docmill 도구(Python)로 만든다.

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| 새 저장소 docmill-studio | 분리가 깔끔함 | 시크릿·보호 규칙·CodeRabbit·워크플로를 다시 세팅, 도구를 패키지로 배포해야 함 | 세팅 비용, 이 세션에서 저장소 생성 불가 |
| 인수 테스트를 Node Playwright(@playwright/test)로 | Electron을 직접 띄울 수 있음 | 테스트 러너·FR 필터·보호 경로를 두 벌 관리 | pytest 하나로 FR 필터(conftest)를 그대로 쓰는 게 단순함 |
| rhwp 공개 데모(edwardkim.github.io) 사용 | 빌드 불필요 | 외부 사이트 의존, 오프라인 불가 | 셀프 빌드가 Rust 없이 가능해서 |
| 편집기 직접 구현 | 완전한 제어 | 몇 년짜리 일 | 범위 밖 |

## 결과
- 좋아지는 점: 지금 파이프라인(이슈 → 구현 → CI → 리뷰 → 머지)을 그대로 쓴다. 기능 대부분이 pytest로 판정된다.
- 감수하는 점: 모든 CI·구현 잡이 Node 설치, rhwp-studio 빌드(캐시), ONLYOFFICE 기동(1~2분)을 거친다.
  화면 품질은 테스트가 못 잡으므로 단계마다 스크린샷으로 사람이 확인한다.
- 다시 볼 조건: CI 시간이 잡당 15분을 넘으면 ONLYOFFICE가 필요 없는 잡에서 끄는 입력을 쓴다.
  ONLYOFFICE는 AGPL이라 앱을 배포할 때 라이선스를 다시 확인한다 (지금은 별도 서버로만 띄운다).
- 참고: `@rhwp/core`는 Node에서도 동작한다 (`HwpDocument.getTextFileText()`로 hwp 텍스트 추출 확인). docmill 도구가 hwp를 읽을 때 쓸 수 있다.
