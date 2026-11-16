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
