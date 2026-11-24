# phase9 샘플

새로 필요한 샘플 없음. 기존 `tests/fixtures/sales_week1.xlsx`, `sales_week2.xlsx`(머리글 `부서·월·매출`)를 쓴다.
- FR-49: 입력 파일 이름만 다른 복사본을 테스트가 tmp 폴더에 만든다.
- FR-50: `sales_week47.xlsx`는 sales_week2.xlsx 복사본, 열이 빠진 파일은 week1의 `매출` 머리글을 `금액`으로 바꿔 테스트가 만든다.
