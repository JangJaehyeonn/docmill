# docmill 작업 규칙 (Claude Code용)

이 저장소는 사람 개입을 최소화한 자동 개발 파이프라인으로 운영된다.
Claude Code는 이 문서를 최우선 규칙으로 따른다.

## 프로젝트 한 줄 요약
엑셀·PPT·워드 작업을 사용자의 AI 채팅에서 처리하고, 성공한 작업을 레시피로 저장해 AI 없이 반복 실행하는 MCP 서버.
문서 편집은 GridCraft(xlsx)·DeckCraft(pptx)·WordCraft(docx) 엔진의 헤드리스 MCP 서버를 내부에서 호출해서 한다.

## 기술 스택
- Python 3.11+ / uv / MCP Python SDK 2.x (`mcp.server.mcpserver.MCPServer`, FastMCP 아님)
- CLI: Typer / 저장소: SQLite / 테스트: pytest / 린트: ruff / 타입: mypy strict
- 결정 배경은 `docs/adr/`를 읽고, 새 결정은 ADR로 남긴다.

## 작업 흐름 (이슈 하나 = PR 하나)
1. 이슈 본문의 **요구사항 ID(FR-xx), 인수 기준, 의존 이슈**를 먼저 읽는다. 의존 이슈가 닫히지 않았으면 작업하지 않고 이슈에 코멘트를 남긴다.
2. 브랜치: `claude/<이슈번호>-<짧은-영문-설명>` (예: `claude/12-excel-report-tool`).
3. 구현과 단위 테스트(`tests/unit/`)를 함께 작성한다.
4. 커밋 전 반드시 실행하고 전부 통과시킨다:
   ```
   uv run ruff check . && uv run ruff format --check . && uv run mypy && DOCMILL_ACTIVE_FRS=FR-xx uv run pytest -q
   ```
   `FR-xx`는 이 이슈의 요구사항 ID다. CI는 이미 끝난 요구사항의 인수 테스트까지 함께 돌린다.
5. 커밋 메시지는 Conventional Commits: `feat: ...`, `fix: ...`, `test: ...`, `docs: ...`, `chore: ...`. 본문 끝에 `Refs #<이슈번호>`.
6. PR 제목: `feat(FR-05): excel_to_report_ppt 구현`. 본문은 PR 템플릿을 채우고 `Closes #<이슈번호>`를 넣는다.
7. PR을 연 뒤 `gh pr merge --auto --squash <PR번호>`로 자동 머지를 예약한다. 다른 방식의 머지는 하지 않는다.

## 절대 하지 말 것 (보호 규칙)
- `tests/acceptance/`, `scripts/`, `.github/`, `CLAUDE.md`, `.claude/`, `.coderabbit.yaml`, `docs/requirements/`, `ruff.toml`, `mypy.ini`, `pytest.ini`를 수정하지 않는다. 이 경로는 사람 승인으로만 바뀐다.
- 테스트를 통과시키려고 테스트 기대값을 바꾸거나 `skip`·`xfail`을 붙이지 않는다. 구현을 고친다.
- 요구사항에 없는 기능을 추가하지 않는다. 필요해 보이면 이슈에 제안 코멘트만 남긴다.
- 비밀값·토큰·개인 정보를 코드, 로그, 테스트 샘플에 넣지 않는다.
- `main`에 직접 푸시하지 않는다.

## 설계 원칙
- AI에 노출하는 MCP 도구는 **업무 단위 도구 6개**뿐이다 (`excel_to_report_ppt`, `doc_to_task_table`, `apply_template`, `verify_numbers`, `save_recipe`, `run_recipe`). 엔진의 저수준 도구는 `src/docmill/engines/` 안에서만 쓴다.
- 엔진은 반드시 `DocumentTool` 인터페이스(`src/docmill/engines/base.py`)로 감싼다.
- **재현성**: 도구 내부 로직은 같은 입력이면 같은 결과를 내야 한다. 현재 시각, 난수, 정렬되지 않은 dict·set 순회, 환경에 따라 달라지는 경로를 결과에 넣지 않는다. 날짜가 필요하면 인자로 받는다.
- 레시피는 엔진 명령이 아니라 **docmill 도구 호출과 인자**를 저장한다. 입력 파일은 `$변수`로 바꾼다.
- 파일 작업은 작업별 임시 폴더 안에서만 하고, 엔진 프로세스에는 시간 제한(기본 60초)을 둔다.
- 엔진 실패는 예외로 서버를 죽이지 말고, 실패한 단계와 원인을 도구 결과로 돌려준다.
- stdio 모드에서는 stdout이 MCP 통신 채널이다. `print`나 로그를 stdout에 쓰지 말고 진단 메시지는 stderr(logging)로 보낸다.
- 모든 도구 호출은 실행 ID와 함께 SQLite 로그에 남긴다.

## ADR (결정 기록)
새 의존성 추가, 모듈 구조 변경, 저장 형식 결정 등 "왜 이렇게 했는지" 설명이 필요한 변경은
`docs/adr/NNNN-짧은-제목.md`를 같은 PR에 추가한다. 형식은 `docs/adr/0000-template.md`를 따른다.

## 막혔을 때
- 같은 문제로 3번 시도해도 해결되지 않거나, 요구사항이 모순되거나, 보호 경로 수정이 필요하면 작업을 멈춘다.
- 이슈(또는 PR)에 무엇이 막혔는지, 시도한 것, 필요한 결정을 정리해 코멘트하고 `needs-human` 라벨을 붙인다.

## 코드 스타일
- 코드 식별자·주석은 영어, 사용자에게 보이는 메시지·문서·docstring 설명은 한국어로 쓴다.
- 함수는 짧게, 타입 힌트는 빠짐없이. 공개 함수에는 docstring을 쓴다.
