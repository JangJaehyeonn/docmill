## 요약
<!-- 무엇을 왜 바꿨는지 2~3줄 -->

Closes #

## 요구사항
- 요구사항 ID: FR-
- 인수 기준 충족 여부 (이슈의 체크리스트를 그대로 옮겨 체크)
  - [ ]

## 쓴 기술과 선택 이유
<!-- 새 라이브러리·구조 결정이 있으면 docs/adr/ 문서 링크 -->

## 검증
- [ ] `uv run ruff check .`
- [ ] `uv run mypy`
- [ ] `uv run pytest -q`
- [ ] `DOCMILL_ACTIVE_FRS=FR-xx uv run pytest -q tests/acceptance`

## 보호 경로
- [ ] `tests/acceptance/`, `.github/`, `.claude/`, `CLAUDE.md`, `.coderabbit.yaml`, `docs/requirements/`를 수정하지 않았다
