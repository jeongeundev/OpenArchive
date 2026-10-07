/**
 * 토큰 만료일 입력과 표시 (ADR-061 결정 1).
 *
 * 화면은 날짜만 고른다. 고른 날짜는 그날 끝까지 유효하므로 브라우저 로컬 시간대의
 * 다음 날 00:00을 보낸다. 만료 여부 판정은 서버의 `expired`가 하고, 여기서는 하지 않는다.
 */

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function localDate(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** `"YYYY-MM-DD"` → 다음 날 로컬 00:00의 ISO 문자열. 비어 있으면 만료 없음(`null`). */
export function expiresAtFromDate(date: string): string | null {
  if (date === "") return null;
  const [year, month, day] = date.split("-").map(Number);
  return new Date(year, month - 1, day + 1).toISOString();
}

/** 만료 시각을 사람이 읽는 말로. 로컬 자정 정각이면 날짜 입력으로 고른 그 전날까지로 보인다. */
export function formatExpiry(expiresAt: string | null): string {
  if (expiresAt === null) return "없음";
  const at = new Date(expiresAt);
  const midnight =
    at.getHours() === 0 && at.getMinutes() === 0 && at.getSeconds() === 0 && at.getMilliseconds() === 0;
  if (midnight) {
    return `${localDate(new Date(at.getFullYear(), at.getMonth(), at.getDate() - 1))}까지`;
  }
  return `${localDate(at)} ${pad(at.getHours())}:${pad(at.getMinutes())}까지`;
}

/** 날짜 입력의 `min` — 오늘(로컬). */
export function todayInputValue(): string {
  return localDate(new Date());
}
