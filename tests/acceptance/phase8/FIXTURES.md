# Phase 8 인수 테스트 샘플

새로 만들어야 하는 고정 샘플은 없다. `tests/fixtures/`의 `sales_q3.xlsx`·`company_intro.pptx`(3장)·`meeting_1111.docx`를 그대로 쓴다.
나머지 docx(직함 서명, 주민등록번호, 주간 보고, 회의록)는 테스트가 표준 라이브러리 `zipfile`로 직접 만든다.

| FR | 쓰는 샘플 | 비고 |
| --- | --- | --- |
| FR-44 | `sales_q3.xlsx`(A1:C16), `company_intro.pptx` | 영업1팀 C2=1200 을 1500으로 바꿔 원본 변경을 흉내 낸다 |
| FR-45 | `meeting_1111.docx`, 테스트가 만든 docx | 설정 파일 `.docmill/settings.json` |
| FR-46 | `company_intro.pptx`(3장), 테스트가 만든 docx | 개인정보 실패용 문서에는 가짜 주민등록번호 `900101-1234567` |
| FR-47 | `meeting_1111.docx`, `sales_q3.xlsx` | provenance.json `sentences` |
| FR-48 | 테스트가 만든 `weekly_a/b/c.docx`, `minutes.docx` | `os.utime`으로 수정 시각을 고정 |

환경 요구:

- FR-44·FR-46(PDF)·FR-47 studio 테스트: `DOCMILL_ONLYOFFICE_URL`(없으면 실패).
- 모든 studio 테스트: `npm --prefix studio run build`로 만든 `studio/out/server.js`.
- 채팅 에이전트는 테스트가 만든 가짜 stream-json 명령(`DOCMILL_STUDIO_AGENT_CMD`)을 쓴다. 실제 Claude는 부르지 않는다.
- `tests/acceptance/conftest.py`의 `start_studio`·`page` 픽스처를 쓴다.
- `company_intro.pptx`는 개인정보(이메일) 점검이 실패하므로 FR-46 PDF 테스트는 확인 창에서 `그래도 내보내기`를 누른다.
