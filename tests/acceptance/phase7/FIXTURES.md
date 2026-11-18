# Phase 7 인수 테스트 샘플

기존 `tests/fixtures/`의 `sales_q3.xlsx`·`company_intro.pptx`(3장)·`meeting_1111.docx`·`meeting_1111.hwp`는 그대로 쓴다.
나머지 docx·pptx·xlsx는 테스트가 표준 라이브러리 `zipfile`로 직접 만든다.
새로 필요한 고정 샘플은 hwp 두 개다 (hwp는 테스트에서 만들 수 없다). 두 파일 모두 `tests/fixtures/`에 넣었다 (만든 방법은 `tests/fixtures/README.md`).

| 파일 | 쓰는 곳 | 내용·만드는 방법 |
| --- | --- | --- |
| `tests/fixtures/official_style.hwp` | FR-38 | 아래 문단 6개를 담은 한글 파일. rhwp-studio에서 새 문서에 같은 문장을 입력하고 `exportBytes('hwp')`로 저장한다 (`meeting_1111.hwp`와 같은 방법). |
| `tests/fixtures/meeting_1111_v2.hwp` | FR-41 | `meeting_1111.hwp`에서 **숫자 두 곳**과 **문장 한 곳**만 바꾼 버전 (문단 수는 같고 나머지 글자는 그대로). 원본에 숫자가 없으면 두 문단에 숫자를 넣은 버전을 `meeting_1111.hwp`와 짝으로 새로 만들어도 된다. 그때는 이 표와 `tests/fixtures/README.md`를 고친다. |

`official_style.hwp` 문단 (FR-38 테스트의 `PARAGRAPHS`와 같아야 한다):

1. `행사 개최 안내`
2. `1. 일시: 2026-11-16 14:30`
3. `2. 장소: 본관 대회의실`
4. `3. 예산: 10,150원`
5. `1) 세부 일정은 별도 안내`
6. `2026년 11월 17일에 다시 알린다.`

환경 요구:

- FR-37 studio 테스트: `DOCMILL_ONLYOFFICE_URL`(없으면 실패).
- FR-39 studio 테스트·FR-37 studio 테스트: `npm --prefix studio run build`로 만든 `studio/out/server.js`.
- FR-42: 실제 이미지 제공자는 부르지 않는다. 가짜 제공자는 환경 변수 `DOCMILL_FAKE_IMAGE_PNG`(테스트가 만든 PNG)를 쓴다.
