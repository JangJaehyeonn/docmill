# Phase 6 인수 테스트 샘플

새로 필요한 고정 샘플은 없다. 테스트가 쓰는 파일은 모두 `tests/fixtures/`에 이미 있다.

| 파일 | 쓰는 곳 |
| --- | --- |
| `sales_q3.xlsx` | FR-26·27·29·30·31·33 |
| `company_intro.pptx` | FR-26·27·31 (슬라이드 2에 제목 도형 id=2, 본문 도형 id=3) |
| `meeting_1111.docx` | FR-26·27·29·30·32·35 |
| `meeting_1111.hwp` | FR-26·28·35 |

테스트 안에서 만드는 파일:

- FR-33: `excel_to_report_ppt` 결과 pptx에 zip을 고쳐 출처 없는 숫자·예측 문장·바뀐 차트 값을 넣는다. 출처 파일은 결과 옆 `.provenance.json`을 복사한다.
- FR-34: 표준 라이브러리 `zipfile`로 만든 최소 docx (날짜·요일 불일치, 주민등록번호 형식, 자리표시자, 깨끗한 문서).
- FR-35: 읽을 수 없는 hwp는 일반 텍스트 바이트를 `.hwp` 이름으로 저장해 만든다.

환경 요구:

- FR-27: `DOCMILL_ONLYOFFICE_URL`(없으면 테스트가 실패), FR-28: `RHWP_STUDIO_DIST`(없으면 실패).
- 나머지 studio 테스트는 `npm --prefix studio run build`로 만든 `studio/out/server.js`가 필요하다.
