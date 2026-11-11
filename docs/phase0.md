# Phase 0 · 엔진 검증 기록

Phase 0은 사람이 직접 한다. 결과는 이후 이슈와 인수 테스트의 근거가 된다.

## 환경
- OS:
- GridCraft 버전:
- DeckCraft 버전:
- WordCraft 버전:

## 연결
```
claude mcp add gridcraft -- gridcraft-cli mcp
claude mcp add deckcraft -- deckcraft-cli mcp
claude mcp add wordcraft -- wordcraft-cli mcp
```

## 결과
| 엔진 | 시험한 요청 | 생성 | 다른 앱에서 열기 | 문제점 | 쓸 만한 MCP 도구 |
| --- | --- | --- | --- | --- | --- |
| GridCraft | 지역·매출 20행, 지역별 합계 표, 막대 차트 → test.xlsx | | | | |
| DeckCraft | 슬라이드 3장, 2번 장에 막대 차트, PNG 렌더링 | | | | |
| WordCraft | 회의록 docx 생성, 담당자·마감일 있는 할 일 추출 | | | | |

## 재현성
같은 작업을 CLI로 두 번 실행하고 `gridcraft-cli cat` 등으로 내용을 비교한다.
- 결과:

## 가정 확인 (요구사항 정의서 v2 4장)
- [ ] 세 엔진의 헤드리스 MCP 서버가 실행된다
- [ ] GridCraft로 합계·피벗과 수식 재계산(eval)이 된다
- [ ] DeckCraft로 슬라이드 생성·그래프 삽입·서식 변경이 된다
- [ ] WordCraft로 본문 텍스트 추출·서식 변경이 된다
- [ ] 같은 입력에 같은 결과가 나온다

## 결론
- Phase 1 진행 여부:
- 미루는 기능:
