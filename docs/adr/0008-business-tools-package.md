# 0008. 업무 도구는 `docmill.tools` 패키지에 두고, 실행 ID는 contextvar로 전달한다

- 상태: 채택
- 날짜: 2026-11-14
- 관련: FR-05, FR-16

## 배경
업무 도구 6개를 `server.py`에 모두 넣으면 파일이 커진다. 또 도구 결과에 실행 ID를 넣어야 하는데, 실행 ID는 `logged` 래퍼가 발급하고 도구 함수에는 전달되지 않는다.

## 결정
- 업무 도구 구현은 `src/docmill/tools/<도구>.py`에 두고, `server.py`는 등록만 한다.
- `runlog.logged`가 호출 동안 `contextvars`에 실행 ID를 설정하고, 도구는 `runlog.current_run_id()`로 읽는다. 래퍼 밖(단위 테스트 등)에서는 `None`이므로 새 ID를 발급한다.
- `excel_to_report_ppt`의 결과는 JSON 문자열(`path`·`summary`·`run_id`·`verification`)이고, 실패하면 `error`와 실패한 `step`을 담는다.
- 산출물은 `DOCMILL_HOME/outputs/<실행 ID>.pptx`에 둔다. 작업은 임시 폴더에서 하고 성공했을 때만 복사한다.
- `template` 인자는 DeckCraft 테마 이름으로 해석한다.
- 숫자 검증은 저장된 pptx의 그래프 캐시 값을 엑셀 합계와 대조한다(그룹별 1건 + 총합 1건).

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| 도구 함수가 `run_id` 인자를 받음 | 단순 | AI에 노출되는 스키마가 오염됨 | 도구 인자는 업무 인자만 |

## 결과
- 이후 도구(FR-06~FR-10)도 같은 방식으로 실행 ID를 결과에 넣을 수 있다.
