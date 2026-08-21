/** 비밀번호 변경은 세션을 끊는다 — 화면이 곧바로 `/login`으로 밀리므로, 성공 사실을 그 화면까지 옮긴다. */
const KEY = "openarchive:password-changed";

export function markPasswordChanged(): void {
  sessionStorage.setItem(KEY, "1");
}

/** 순수 조회. 읽기와 지우기를 나눈 것은 렌더 중에 부수효과를 내지 않기 위해서다. */
export function wasPasswordChanged(): boolean {
  return sessionStorage.getItem(KEY) !== null;
}

/** 한 번 보여준 뒤 지운다. 남겨두면 새로고침이나 재방문에 지난 일이 방금 일처럼 뜬다. */
export function clearPasswordChangedNotice(): void {
  sessionStorage.removeItem(KEY);
}
