# Phase 0 · 엔진 검증 기록

Phase 0은 사람이 직접 한다. 결과는 이후 이슈와 인수 테스트의 근거가 된다.

> 2026-11-12 검증은 Claude Code(Opus 5.5)가 대신 실행했다. 작업 폴더는 `C:\dev\phase0-work` 하나만 썼고, 엔진은 MCP 도구로만 조작했다. 확인은 각 엔진 CLI(`cat`·`eval`·`text`·`info`·`check`)로 했다. 아래 내용은 모두 직접 실행해 확인한 것이다. 확인하지 못한 항목은 "미확인"으로 적었다.

## 환경
- OS: Windows 11 Pro 10.0.26200 (`ver`: Microsoft Windows [Version 10.0.26200.9711])
- GridCraft 버전: `gridcraft-cli version` → `gridcraft-cli 0.9.0`
- DeckCraft 버전: `deckcraft-cli version` → `deckcraft-cli 0.9.0`
- WordCraft 버전: `wordcraft-cli version`은 지원되지 않는다(`unknown command 'version'` 후 도움말 출력). 대신 `wordcraft-cli --version` → `wordcraft-cli 0.8.0`

## 연결
```
claude mcp add gridcraft -- gridcraft-cli mcp
claude mcp add deckcraft -- deckcraft-cli mcp
claude mcp add wordcraft -- wordcraft-cli mcp
```
- `claude mcp list`에는 `gridcraft`만 등록돼 있었다(✔ Connected). `deckcraft`와 `wordcraft`는 등록돼 있지 않아 Claude Code 세션에서 도구로 쓸 수 없었다.
- 우회 방법: 같은 서버(`deckcraft-cli mcp`, `wordcraft-cli mcp`, 인자 없음)를 직접 띄웠다. 작은 stdio JSON-RPC 클라이언트로 `initialize` → `tools/list` → `tools/call`을 보내 MCP 도구를 호출했다. 두 서버 모두 `Backend: headless`로 응답했고 모든 호출이 정상 처리됐다.
- Phase 1 전에 위 두 줄의 `claude mcp add`를 실행해 등록해야 한다.

## 결과
| 엔진 | 시험한 요청 | 생성 | 다른 앱에서 열기 | 문제점 | 쓸 만한 MCP 도구 |
| --- | --- | --- | --- | --- | --- |
| GridCraft | 지역·매출 20행, 지역별 합계 표, 막대 차트 → test.xlsx | 성공. `new_workbook` → `write_range`(5지역 × 4개월 = 20행, E1:F7에 `SUMIF` 합계와 `SUM` 총계) → `format_range`(머리글 굵게) → `insert_chart`(column) → `save_workbook`.<br>`gridcraft-cli cat`으로 원본 20행과 합계(서울 5150, 부산 3530, 대구 2600, 인천 3070, 광주 1960, 총계 16310)를 확인했고 손계산과 일치했다. `info`: 막대 차트 1개(ColumnClustered, H2).<br>`gridcraft-cli eval '=SUM(C2:C21)' --in test.xlsx` → 16310.<br>재계산: `run --in test.xlsx --cmd 'cell.set={"cell":"C2","input":"2200"}' --print E1:F7`을 실행하면 서울 6150, 총계 17310으로 다시 계산된다.<br>`check`: 수식 6개 모두 일치, 라운드트립 ok.<br>피벗: `list_commands "pivot"`에 `insert.pivotTable`, `pivot.addField`, `pivot.refresh`, `insert.pivotChart` 등 24개가 있다. 실제로 `insert.pivotTable`(rows 지역, values 매출 sum)을 실행하니 SUMIF 결과와 같은 값이 나왔고 Grand Total은 16310이었다(pivot.xlsx). | 미확인. 이 PC에 Excel(Office16)은 설치돼 있으나, COM으로 여는 확인 스크립트가 실행 권한 검사에서 거부돼 열어보지 못했다. 엔진 자체 라운드트립 검사(`check`)만 통과했다. | ① 피벗은 전용 래퍼 도구가 없어 `execute_command insert.pivotTable`로만 만들 수 있다.<br>② 피벗 머리글이 영어(`Row Labels`, `Sum of 매출`, `Grand Total`)로 고정된다. | `open_workbook`, `read_range`(값·수식·표시 텍스트), `write_range`, `evaluate_formula`, `insert_chart`, `format_range`, `create_table`, `save_workbook`, `inspect_workbook`, `execute_command`(`insert.pivotTable`, `range.setValues`)<br>→ `excel_to_report_ppt`(엑셀 읽기·집계), `verify_numbers`(`evaluate_formula`/`read_range`로 대조), `apply_template`(`format_range`), `save_recipe`/`run_recipe`(명령 ID+JSON 인자가 `run --cmd`와 같은 형식이라 그대로 저장·재생할 수 있다) |
| DeckCraft | 슬라이드 3장, 2번 장에 막대 차트, PNG 렌더링 | 성공. `new_presentation` → `add_slide`(titleOnly) + `insert_chart`(column, 5지역 합계) → `add_slide`(titleAndContent, 요약 3줄) → `save_presentation` test.pptx(3장, chart1.xml 포함).<br>서식 변경: `set_text`로 제목을 넣고 `select` → `run_command format.bold {on:true}`, `format.size {size:48}`를 적용했다. 다시 연 파일의 slide1.xml에서 `<a:rPr sz="4800" b="1"/>`을 확인했다.<br>렌더링: `render_slide {slide:1}`는 PNG 960×540을 반환했고 `export {format:png, slide:1}`은 slide2.png 1920×1080을 만들었다. 이미지를 직접 열어 막대 5개와 축·범례를 확인했다. | 미확인(PowerPoint 설치됨, 위와 같은 이유로 열어보지 못함) | ① Claude Code에 MCP 서버가 등록돼 있지 않았다(연결 절 참고).<br>② `insert_chart`에 위치·크기 인자가 없다. 기본 위치에 들어간 차트 제목이 titleOnly 레이아웃의 슬라이드 제목과 겹쳤다(PNG로 확인).<br>③ `inspect_slide`의 인자는 `slide`가 아니라 `index`다. `{slide:0}`을 보내면 `unknown argument "slide" (expected: index)` 오류가 나고, `index`로 고치면 성공한다.<br>④ `inspect_slide`는 글자 단위 서식(굵게, 크기)을 보여주지 않아 XML로 확인해야 했다. | `new_presentation`, `open_presentation`, `add_slide`, `insert_chart`(categories+series를 직접 받음), `insert_table`, `set_text`, `select`+`run_command`(`format.*`), `inspect_document`/`inspect_slide`, `render_slide`/`export`(png·pdf), `save_presentation`, `batch`<br>→ `excel_to_report_ppt`(차트·요약 슬라이드 생성), `apply_template`(테마·서식), `verify_numbers`(도구 설명상 `inspect_slide`가 차트 정보를 반환. 차트 값 반환은 미확인) |
| WordCraft | 회의록 docx 생성, 담당자·마감일 있는 할 일 추출 | 성공. `new_document` → `type_text {paragraphs:true}`(제목·일시·참석·안건 + 할 일 3개. 형식: `N. 내용 / 담당: 이름 / 마감: YYYY-MM-DD`) → `select_text "주간 회의록"` → `execute format.bold`, `format.size {size:20}` → `save_document` test.docx.<br>`wordcraft-cli text test.docx`로 8줄 본문을 그대로 추출했다. 정규식으로 할 일 3개(내용·담당자·마감일)를 모두 뽑았다. 서식은 document.xml의 `<w:b/>`, `<w:sz w:val="40"/>`(20pt)로 확인했다. | 미확인(Word 설치됨, 위와 같은 이유로 열어보지 못함) | ① Claude Code에 MCP 서버가 등록돼 있지 않았다(연결 절 참고).<br>② 도움말에는 "`--automation-write-root`가 없으면 쓰기 권한이 없다"고 돼 있다. 하지만 인자 없이 띄운 헤드리스 서버에서 `save_document`가 성공했다. docmill에서는 작업별 임시 폴더를 read/write root로 명시해 띄워야 한다.<br>③ 저장할 때마다 `docProps/core.xml`에 생성·수정 시각이 들어간다(재현성 참고). | `new_document`, `open_document`, `get_text`(본문 텍스트), `inspect_document`(블록·스타일·표 구조), `type_text`, `select_text`+`execute`(`format.*`, `para.style`, `insert.table`), `save_document`, `render_page`, `batch`<br>→ `doc_to_task_table`(`get_text`/`inspect_document`로 할 일 추출), `apply_template`(스타일·서식), `verify_numbers`(본문 숫자 대조) |

## 재현성
같은 작업을 CLI로 두 번 실행하고 `gridcraft-cli cat` 등으로 내용을 비교한다.
- 결과: **GridCraft는 같다.**
  - 위 GridCraft 작업을 `gridcraft-cli run --quiet --cmd 'range.setValues=…'`(원본 20행) `--cmd 'range.setValues=…'`(SUMIF 합계표) `--cmd 'home.bold=…'` `--cmd 'insert.chart=…' --out runN.xlsx` 스크립트로 바꿔 두 번 실행했다.
  - `gridcraft-cli cat`(값), `cat --formulas`(수식), `info` 출력을 `diff`로 비교했더니 모두 같았다.
  - run1.xlsx와 run2.xlsx는 바이트 단위로도 같다(SHA-256 `36177360…1f0e`). MCP로 만든 test.xlsx도 같은 해시였다.
- 참고(추가 확인):
  - DeckCraft: 같은 MCP 호출을 두 번 해서 저장한 pptx 2개가 바이트 단위로 같았다.
  - WordCraft: 같은 MCP 호출을 두 번 해서 저장한 docx 2개는 `docProps/core.xml`의 `dcterms:created`/`modified` 시각만 달랐다. 나머지 파트와 `wordcraft-cli text` 출력은 같았다. docmill에서 결과를 비교할 때는 core.xml을 빼거나 정규화해야 한다.

## 가정 확인 (요구사항 정의서 v2 4장)
- [x] 세 엔진의 헤드리스 MCP 서버가 실행된다. 세 서버 모두 `<엔진>-cli mcp`로 헤드리스 실행되고 `tools/call`에 응답했다. 단 Claude Code에 등록된 것은 gridcraft뿐이었다.
- [x] GridCraft로 합계·피벗과 수식 재계산(eval)이 된다
- [x] DeckCraft로 슬라이드 생성·그래프 삽입·서식 변경이 된다
- [x] WordCraft로 본문 텍스트 추출·서식 변경이 된다
- [x] 같은 입력에 같은 결과가 나온다. GridCraft·DeckCraft는 바이트 단위로 같았다. WordCraft는 core.xml의 시각만 다르다.

## 결론
- Phase 1 진행 여부: **진행한다.** 6개 업무 도구에 필요한 엔진 기능(읽기·쓰기·수식 평가·피벗·차트·슬라이드 생성·렌더링·텍스트 추출·서식)이 모두 MCP 도구로 동작했다. 다만 착수 전에 다음을 마친다.
  1. deckcraft·wordcraft를 `claude mcp add`로 등록한다.
  2. Excel/PowerPoint/Word에서 test.xlsx·test.pptx·test.docx가 열리는지 사람이 확인한다(이번에는 미확인).
- 미루는 기능:
  - DeckCraft 차트의 위치·크기 자동 배치. 지금은 기본 위치에 들어가 제목과 겹치므로, Phase 1에서는 레이아웃을 `blank`로 쓰거나 `run_command`로 위치를 조정하는 정도로 처리한다.
  - GridCraft 피벗 머리글 현지화(`Row Labels` 등)
  - WordCraft 문서 메타데이터(core.xml 시각) 고정. 재현성 비교에서 제외하는 것으로 대신한다.
  - 다른 앱(Office) 호환성 자동 검증
