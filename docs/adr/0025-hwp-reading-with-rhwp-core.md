# 0025. 한글 파일 읽기: @rhwp/core를 Node 스크립트로 호출

- 상태: 채택
- 날짜: 2026-11-18
- 관련: FR-35, #102

## 배경
doc_to_task_table·review_map·preflight가 hwp·hwpx 입력도 받아야 한다. 결과 형식은 docx와 같아야 한다.
ADR 0020에서 `@rhwp/core`가 Node에서 hwp 텍스트를 추출함을 확인했다.

## 결정
`studio/package.json`에 `@rhwp/core` 0.9.0을 추가하고(rhwp 편집기와 같은 버전), 작은 Node 스크립트
`src/docmill/tools/hwp_text.mjs`가 문단 목록을 JSON으로 내보낸다. Python은 `tools/hwp.py`에서 이를 시간 제한(60초)과 함께 호출하고
`DocumentTool` 어댑터(`engines/rhwp.py`의 `RhwpTool`)가 원본을 작업 폴더로 복사해 읽고, 글자는 CP949 밖 문자를 보존하는 `getTextFileUnicode()`로 얻는다.
실패는 `EngineResult`로 돌려받아 `HwpError`로 바꿔 각 도구가 단계(step)와 이유로 돌려준다. 폴더는 `DOCMILL_RHWP_CORE`로 바꿀 수 있다.

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| Python으로 HWP5 직접 파싱 | Node 불필요 | 형식 구현·유지 비용, hwpx 별도 | rhwp가 이미 검증됨 |
| rhwp CLI | 단순 | 별도 Rust 빌드·배포 | CI에 이미 npm 경로가 있음 |

## 결과
hwp·hwpx를 docx와 같은 문단 규칙으로 검토한다. 감수하는 점: 한글 읽기에는 Node와 studio 의존성 설치가 필요하다. 표 서식·제목 스타일 점검은 hwp에서 하지 않는다.
