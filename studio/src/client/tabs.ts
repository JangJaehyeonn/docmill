export interface OpenFile {
  path: string;
  name: string;
}

export interface TabState {
  tabs: OpenFile[];
  active: string | null;
}

/** 파일을 탭으로 연다. 이미 열려 있으면 그 탭을 활성으로 만든다. */
export function openTab(state: TabState, file: OpenFile): TabState {
  const tabs = state.tabs.some((t) => t.path === file.path) ? state.tabs : [...state.tabs, file];
  return { tabs, active: file.path };
}

/** 탭을 닫는다. 활성 탭을 닫으면 이웃 탭이 활성이 된다. */
export function closeTab(state: TabState, path: string): TabState {
  const index = state.tabs.findIndex((t) => t.path === path);
  if (index < 0) return state;
  const tabs = state.tabs.filter((t) => t.path !== path);
  if (state.active !== path) return { tabs, active: state.active };
  return { tabs, active: (tabs[index] ?? tabs[index - 1])?.path ?? null };
}
