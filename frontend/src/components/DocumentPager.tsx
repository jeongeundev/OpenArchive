"use client";

import { useEffect } from "react";

const BUTTON_CLASS =
  "rounded border border-neutral-700 px-3 py-1 text-neutral-300 hover:border-neutral-500 disabled:cursor-not-allowed disabled:opacity-40";

export function DocumentPager({
  page,
  pageSize,
  total,
  onChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
}): React.ReactElement | null {
  const lastPage = Math.max(0, Math.ceil(total / pageSize) - 1);

  // 삭제로 문서가 줄면 보던 페이지가 빈다 — 빈 표 대신 마지막 페이지를 보인다.
  useEffect(() => {
    if (page > lastPage) onChange(lastPage);
  }, [page, lastPage, onChange]);

  if (total <= pageSize) return null;

  const first = page * pageSize + 1;
  const last = Math.min(total, (page + 1) * pageSize);

  return (
    <nav aria-label="문서 목록 페이지" className="flex items-center justify-end gap-3 text-sm">
      <span className="text-neutral-400">
        {total}건 중 {first}–{last}
      </span>
      <button
        className={BUTTON_CLASS}
        disabled={page === 0}
        onClick={() => onChange(page - 1)}
        type="button"
      >
        이전
      </button>
      <button
        className={BUTTON_CLASS}
        disabled={page >= lastPage}
        onClick={() => onChange(page + 1)}
        type="button"
      >
        다음
      </button>
    </nav>
  );
}
