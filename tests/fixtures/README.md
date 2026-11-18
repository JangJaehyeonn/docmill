# 고정 샘플 파일

인수 테스트에 쓰는 xlsx·pptx·docx 샘플. 내용은 모두 직접 지은 가상 데이터다.
테스트가 내용에 기대므로 바꿀 때는 인수 테스트도 함께 확인한다.

| 파일 | 내용 | 쓰는 곳 |
| --- | --- | --- |
| `sales_q3.xlsx` | 첫 행 머리글 `부서·월·매출`, 5개 부서 × 3개월 = 15행 | FR-02·03 열기/저장, FR-05·08 |
| `meeting_1111.docx` | 회의록. "할 일" 아래 `할 일: … / 담당: … / 마감: YYYY-MM-DD` 6개 (완전 4, 담당 또는 마감 빠짐 2) | FR-02·03, FR-06 |
| `company_intro.pptx` | 3장. 제목 32pt·`333333`·왼쪽·Arial, 본문 목록 | FR-02·03, FR-07·18 |
| `template_default.pptx` | 양식. 제목 40pt·`1F4E79`·가운데·Pretendard | FR-07·18 |
| `template_word.docx` | 워드 양식. 제목 1~3: 20pt·`1F4E79`·가운데 / 16pt·`2E75B6`·왼쪽 / 13pt·`404040`·오른쪽, Pretendard | FR-25 |
| `report_headings.docx` | 제목 1~3이 여러 개 있고 본문이 있는 보고서 | FR-25 |
| `sales_week1.xlsx` | `sales_q3.xlsx`와 같은 내용(머리글 `부서·월·매출`) | FR-09 레시피 저장 |

| `sales_week2.xlsx` | `sales_week1.xlsx`와 같은 형식, 다른 매출 숫자 | FR-10 레시피 실행 |

## meeting_1111.hwp

`meeting_1111.docx`와 같은 문단 11개를 한글(HWP 5.0) 형식으로 담은 파일이다 (FR-35, Phase 6 이후 studio 테스트).
rhwp-studio 0.9.0에서 새 문서를 만들어 같은 문장을 입력하고 `exportBytes('hwp')`로 저장했다.
`@rhwp/core` 0.9.0의 `HwpDocument.getTextFileText()`로 읽으면 docx의 문단 텍스트와 같다.

## meeting_empty_title.hwp

`meeting_1111.hwp`에서 3번째 문단("논의 내용")을 `개요 1` 스타일로 바꾸고, 5번째 문단("할 일")의 글자를 지운 뒤
`개요 1` 스타일을 준 파일이다 (FR-35 hwp 빈 제목 점검). `@rhwp/core` 0.9.0의 `applyStyle`·`deleteText`·`exportHwp`로 만들었다.
빈 제목은 구역 1의 문단 5 한 곳이고, 문단 3은 글자가 있는 제목이라 빈 제목이 아니다.

## official_style.hwp

공문서 작성 규칙 검사(FR-38)용 한글 파일. 문단 6개는 `tests/acceptance/phase7/test_fr38.py`의 `PARAGRAPHS`와 같다.
`meeting_1111.hwp`를 `@rhwp/core` 0.9.0으로 열어 남는 문단을 `deleteParagraph`로 지우고, 각 문단 글자를 `deleteText`·`insertText`로 바꾼 뒤 `exportHwp`로 저장했다.

## meeting_1111_v2.hwp

버전 비교(FR-41)용. `meeting_1111.hwp`에서 세 곳만 바꿨다 (문단 수 11개 그대로).
- 문단 4: `일정을 점검했다.` → `진행 상황을 점검했다.` (문장)
- 문단 6: 마감 `2026-11-20` → `2026-11-21` (숫자)
- 문단 7: 마감 `2026-11-15` → `2026-11-14` (숫자)
`@rhwp/core` 0.9.0의 `deleteText`·`insertText`·`exportHwp`로 만들었다.

