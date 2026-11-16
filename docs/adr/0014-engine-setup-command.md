# 0014. `docmill setup`으로 엔진 설치

- 상태: 채택
- 날짜: 2026-11-16
- 관련: FR-04, ADR 0004

## 배경
사용자는 엔진 실행 파일을 직접 받아 PATH에 넣어야 했다. 버전이 어긋나면 재현성이 깨진다.

## 결정
`docmill setup`이 고정 버전(GridCraft 0.9.0, DeckCraft 0.9.0, WordCraft 0.8.0)의 릴리스를
`DOCMILL_HOME/engines/<이름>-<버전>/`에 내려받는다. SHA256SUMS.txt로 확인하고, 실행해서 버전을 출력한다.
이미 설치되어 있으면 내려받지 않는다. 다운로드는 표준 라이브러리 `urllib`을 써서
새 의존성 없이 프록시 환경 변수를 따른다. 엔진 어댑터는 `DOCMILL_*_PATH` → PATH → 설치 폴더 순으로 찾는다.
버전은 `src/docmill/engines/install.py`와 `.github/actions/setup-engines`에 있으므로 올릴 때 함께 바꾼다.

## 검토한 대안
| 대안 | 장점 | 단점 | 버린 이유 |
| --- | --- | --- | --- |
| requests/httpx 사용 | 편함 | 새 의존성 | urllib로 충분 |
| PATH에 있으면 설치 생략 | 빠름 | 버전 보장 없음 | 고정 버전이 목적 |

## 결과
- 현재 Linux x86_64 릴리스만 지원한다. 다른 플랫폼은 `DOCMILL_*_PATH`로 직접 지정한다.
