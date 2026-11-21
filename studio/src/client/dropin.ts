/** 끌어다 놓는 셀 범위가 드래그 데이터에 담기는 형식 이름. */
export const DRAG_TYPE = "application/x-docmill-range";

/** 끌고 있는 셀 범위: 원본 엑셀 경로(작업 폴더 기준), 범위(`A1:C16`), 시트 이름(빈 값이면 첫 시트). */
export interface DraggedRange {
  source: string;
  range: string;
  sheet: string;
}

/** 엑셀 셀 범위(`A1:C16`, `B2`)의 모양이면 true. 서버·docmill 도구도 다시 확인한다. */
export function isCellRange(text: string): boolean {
  return /^\$?[A-Za-z]{1,3}\$?\d{1,7}(:\$?[A-Za-z]{1,3}\$?\d{1,7})?$/.test(text.trim());
}

/** 범위를 이 탭의 문서에 놓을 수 없으면 이유 문구, 놓을 수 있으면 null. */
export function canDropOn(target: string): string | null {
  if (/\.pptx$/i.test(target)) return null;
  if (/\.(docx|hwp|hwpx)$/i.test(target)) {
    return "워드·한글 문서에는 아직 범위를 넣을 수 없어요. PPT 탭에 놓아 주세요.";
  }
  return "PPT 탭에만 범위를 놓을 수 있어요.";
}
