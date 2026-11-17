# 0022. 한글 편집 화면: @rhwp/editor 임베드와 studio 서버 셀프 호스팅

- 상태: 채택
- 날짜: 2026-11-17
- 관련: FR-28, #95

## 배경
hwp·hwpx 탭에 편집 화면이 필요하다. rhwp 편집기는 `file:` 출처를 거부하고, 공개 데모(edwardkim.github.io)는 외부 사이트 의존이라 쓰지 않기로 했다 (ADR 0020).

## 결정
- 새 의존성 `@rhwp/editor` 0.9.0(`createStudio`)으로 iframe 편집기를 붙인다. 편집기 자원은 `RHWP_STUDIO_DIST` 빌드 폴더를 studio 서버가 `/rhwp/` 로 내보낸다 (폴더 밖 경로 거부, wasm MIME 지정).
- 문서는 `GET /api/hwp`로 받아 `loadFile` 하고, 저장은 `hwpctrl.exportBytes`로 내보낸 바이트를 `PUT /api/hwp`로 보내 작업 폴더 원본을 임시 파일 교체 방식으로 바꾼다. PUT 은 `application/octet-stream` 만 받아 다른 웹페이지의 단순 요청 저장을 막는다.
- `window.rhwpEditor` 에 `insertText`·`getText` 두 동작만 노출한다 (hwpctrl `SetTextFile`·`GetTextFile` 사용, 문서 맨 앞에 넣는다).

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| 공개 데모 iframe | 빌드 불필요 | 외부 의존, 오프라인 불가 | ADR 0020 |
| rhwp-studio 를 직접 포크해 번들 | 자유로운 수정 | 유지 비용 | 임베드 API로 충분 |

## 결과
오프라인에서 한글 문서를 열고 저장할 수 있다. 감수하는 점: rhwp 버전을 올리면 열기·저장·`SetTextFile` 동작을 다시 확인해야 한다.
