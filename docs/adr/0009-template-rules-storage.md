# 0009. 양식 규칙은 JSON 파일로 저장하고, 추출은 pptx XML을 읽어서 한다

- 상태: 채택
- 날짜: 2026-11-15
- 관련: FR-07, FR-18

## 배경
FR-18은 양식 PPT에서 제목 규칙(크기·색·정렬·글꼴)을 뽑아 이름으로 저장하고, FR-07 `apply_template`이 그 이름으로 적용해야 한다. 두 요구사항은 서로의 기능을 쓰므로 한 PR로 구현한다.

## 결정
- 저장: `DOCMILL_HOME/templates/<이름>.json` (키 정렬, UTF-8). 같은 양식이면 같은 파일이 나온다.
- 추출: DeckCraft의 `format.state`는 글자 색을 돌려주지 않아, 양식 pptx(zip)의 첫 제목 도형 XML을 표준 라이브러리로 읽기만 한다. 새 의존성은 없다.
- 적용: 문서를 고치는 모든 일은 DeckCraft 어댑터(`edit.select`·`format.fontDialog`·`format.align`)로 한다.
- 규칙 키: `title_size`(pt), `title_color`(RRGGBB), `title_align`(left·center·right·justify), `font`.
- 범위: 지금은 pptx만 지원한다. 워드 양식은 후속 작업이다.

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| SQLite 테이블 | 로그와 같은 저장소 | 규칙이 작아 이점이 적음 | 파일이 더 단순하고 검토하기 쉽다 |
| python-pptx 추가 | 읽기 쉬움 | 새 의존성 | XML 몇 군데만 읽으면 된다 |

## 결과
- 이름에 경로 구분자·제어 문자가 든 양식은 등록을 거부한다.
