# docmill

**엑셀·PPT·워드 작업을 쓰던 AI 채팅(Claude·ChatGPT)에서 처리하고, 성공한 작업을 레시피로 저장해 AI 없이 반복 실행하는 오픈소스 MCP 서버.**

> 상태: 개발 시작 단계 (Phase 0). 아래 기능은 계획이며, 구현 현황은 [이슈](https://github.com/JangJaehyeonn/docmill/issues)에서 확인한다.

## 무엇을 하나

1. AI 앱에 docmill을 MCP 서버로 연결한다.
2. 채팅에서 "이 엑셀로 3분기 보고 PPT 5장 만들어줘"라고 요청하면, AI가 docmill의 업무 도구를 호출해 실제 pptx 파일을 만든다.
3. 결과가 마음에 들면 "레시피로 저장해줘"라고 요청한다.
4. 다음부터는 AI 없이 레시피만 실행한다. 형식은 매번 같고, 숫자만 새 파일 기준으로 바뀐다.

```bash
docmill run "주간 매출 보고" --input 매출_11월2주.xlsx
```

## 제공 도구 (계획)

| 도구 | 하는 일 |
| --- | --- |
| `excel_to_report_ppt` | 엑셀 데이터로 합계·그래프를 만들어 보고 PPT 생성 |
| `doc_to_task_table` | 워드 문서에서 할 일·담당자·마감일을 뽑아 엑셀 표로 정리 |
| `apply_template` | PPT·워드를 등록된 회사 양식으로 통일 |
| `verify_numbers` | 결과 문서의 숫자를 원본 엑셀에서 다시 계산해 대조 |
| `save_recipe` | 성공한 작업을 레시피로 저장 |
| `run_recipe` | 레시피를 새 입력 파일로 AI 없이 재실행 |

## AI 앱에 연결하기

docmill은 stdio MCP 서버다. `uv sync`로 설치하면 `docmill mcp` 명령으로 서버가 실행된다. 보통은 AI 앱이 이 명령을 대신 실행한다.

### Claude Code

```bash
claude mcp add docmill -- uv run --directory /path/to/docmill docmill mcp
```

연결 후 `claude mcp list`로 확인한다.

### Claude 데스크톱

`claude_desktop_config.json`의 `mcpServers`에 추가한 뒤 앱을 다시 시작한다.

```json
{
  "mcpServers": {
    "docmill": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/docmill", "docmill", "mcp"]
    }
  }
}
```

`/path/to/docmill`은 이 저장소를 내려받은 경로로 바꾼다. 서버는 업무 단위 도구만 노출하며 엔진의 저수준 도구는 노출하지 않는다.

## 구조

```
사용자의 Claude / ChatGPT ──MCP──▶ docmill MCP 서버 ──▶ GridCraft · DeckCraft · WordCraft
예약 작업 / CLI ─────────────────▶ run_recipe (AI 호출 0회)
```

문서 편집은 오픈소스 문서 엔진 [GridCraft](https://github.com/storytold/gridcraft)(엑셀), [DeckCraft](https://github.com/storytold/deckcraft)(PPT), [WordCraft](https://github.com/storytold/wordcraft)(워드)가 맡는다.

## 개발 방식

이 저장소는 개발부터 배포까지 자동화되어 있다.

- 이슈 = 요구사항(FR-xx) + 인수 기준
- Claude Code가 이슈를 구현해 PR을 올린다
- CI(ruff·mypy·pytest)가 검증하고 CodeRabbit이 리뷰한다
- 둘 다 통과하면 자동 머지, 릴리스는 사람이 승인한다

자세한 규칙은 [CLAUDE.md](CLAUDE.md)와 [docs/adr](docs/adr)에 있다.

## 로컬 개발

```bash
uv sync
uv run pre-commit install
uv run pytest
```

## 라이선스

MIT. 문서 엔진의 라이선스와 출처는 [NOTICE](NOTICE)를 따른다.
