/** 한글 편집기에서 근거 문단으로 이동할 위치 (구역·문단 0부터, 문단 글자 수). */
export interface HwpTarget {
  section: number;
  paragraph: number;
  length: number;
}

/** 결과 문서의 문장 하나와 근거 (`/api/sentences` 응답 항목, FR-47). */
export interface SentenceRef {
  text: string;
  /** 원본 파일의 작업 폴더 기준 경로. 작업 폴더 밖이거나 없으면 null. */
  source: string | null;
  label: string;
  /** 한글 원본이면 편집기에서 바로 이동할 본문 문단. */
  target?: HwpTarget | null;
}

/** 원본 탭에 보여 줄 근거 강조: 원본 경로, 근거 위치 문구, 누른 문장, (한글이면) 이동할 문단. */
export interface SourceHighlight {
  path: string;
  label: string;
  text: string;
  target: HwpTarget | null;
}

const isIndex = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v) && v >= 0;

function validTarget(value: unknown): HwpTarget | null {
  const t = value as Partial<HwpTarget> | null | undefined;
  if (typeof t !== "object" || t === null) return null;
  return isIndex(t.section) && isIndex(t.paragraph) && isIndex(t.length) && t.length <= 4000
    ? { section: t.section, paragraph: t.paragraph, length: t.length }
    : null;
}

/** 문장을 눌렀을 때 할 일: 원본 탭을 열고 근거를 강조하거나, 열 수 없다는 안내. */
export type SentenceAction =
  | { kind: "highlight"; highlight: SourceHighlight; open: boolean }
  | { kind: "missing"; message: string };

/** 응답 본문에서 형식이 맞는 문장만 꺼낸다. */
export function parseSentenceRefs(body: unknown): SentenceRef[] {
  const list = (body as { sentences?: unknown } | null)?.sentences;
  if (!Array.isArray(list)) return [];
  return list.filter(
    (s): s is SentenceRef =>
      typeof s === "object" &&
      s !== null &&
      typeof (s as SentenceRef).text === "string" &&
      typeof (s as SentenceRef).label === "string" &&
      ((s as SentenceRef).source === null || typeof (s as SentenceRef).source === "string"),
  );
}

/** 누른 문장에 맞는 동작을 정한다. open 은 원본 탭이 아직 열려 있지 않아 새로 열어야 하는지다. */
export function sentenceAction(ref: SentenceRef, openTabs: string[]): SentenceAction {
  if (ref.source === null) {
    return {
      kind: "missing",
      message: `근거 원본을 작업 폴더에서 찾지 못했어요 (근거 위치: ${ref.label}).`,
    };
  }
  return {
    kind: "highlight",
    highlight: {
      path: ref.source,
      label: ref.label,
      text: ref.text,
      target: validTarget(ref.target),
    },
    open: !openTabs.includes(ref.source),
  };
}
