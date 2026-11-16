# 0013. 워드 양식은 수준별 제목 규칙으로 저장하고, 문단 직접 서식으로 적용한다

- 상태: 채택
- 날짜: 2026-11-16
- 관련: FR-25, FR-07, FR-18, ADR 0009

## 배경
FR-25는 FR-07·FR-18을 워드(docx)로 넓힌다. 워드 제목은 1~3 수준마다 서식이 다를 수 있어, PPT의 단일 제목 규칙으로는 담을 수 없다.

## 결정
- 저장: 같은 `templates/<이름>.json`에 두되 `kind: "docx"`와 수준별 규칙 `headings`(`"1"`~`"3"`)를 더한다. 수준 1의 규칙은 최상위 `title_*`·`font`에도 복사해 FR-18 키와 호환한다. `kind`가 없으면 pptx 양식이다.
- 추출: docx(zip)의 `styles.xml`에서 제목 1~3 스타일(`basedOn` 조상 포함)을 표준 라이브러리로 읽는다. 첫 제목 문단에 직접 서식이 있으면 우선한다. 새 의존성은 없다.
- 적용: WordCraft 어댑터로만 한다. 엔진에 스타일 정의 수정 명령이 없어 제목 문단마다 `edit.goto`·`select.paragraph`·`format.size`·`format.color`·`format.font`·`para.align`을 호출한다(문단 직접 서식). 제목 문단 위치는 본문 직계 문단만 센다.
- 종류가 맞지 않는 양식(pptx 양식을 docx에, 그 반대)은 `template` 단계 오류로 돌려준다.

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| styles.xml을 직접 고쳐 저장 | 스타일 한 곳만 바뀜 | 엔진을 거치지 않는 문서 편집 | 편집은 엔진 어댑터로 한다는 원칙 위배 |

## 결과
- 표 안의 제목 문단은 적용 대상이 아니다.
- 테마 색·글꼴(`themeColor`, `asciiTheme`/`hAnsiTheme`/`eastAsiaTheme`)은 `word/theme/theme1.xml`로 풀어 실제 값을 저장한다. `themeShade`/`themeTint`가 있으면 이미 계산된 `w:val`을 쓴다. 테마 파일이 없거나 값을 못 풀면 그 속성은 빠진다.
- 스타일 체인 끝에 `w:docDefaults`를 최하위 계층으로 병합한다.
- 제목 스타일은 ID와 표시 이름(`styles.xml`) 모두로 인식한다. 추출과 적용이 같은 매핑을 쓴다.
- 동아시아 글꼴 `font_ea`는 WordCraft `format.font`의 `eastAsia` 인자(`wordcraft-cli commands --json`로 확인)로 함께 적용한다.
