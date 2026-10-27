// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import type { TagSuggestionsResponse } from "@/lib/types";

// textMissing은 RelatedDocuments와 같다 — 인식 실패 문서에 오지 않을 완료를 약속하지 않는다.
export function TagSuggestions({
  response,
  onApply,
  textMissing = false,
}: {
  response: TagSuggestionsResponse;
  onApply?: (tag: string) => void | Promise<void>;
  textMissing?: boolean;
}): React.ReactElement {
  return (
    <section className="space-y-4 rounded-xl border border-line bg-card p-5">
      <h2 className="text-sm font-semibold text-strong">태그 추천</h2>
      {response.reason === "not_indexed" ? (
        <p className="text-sm text-muted">
          {textMissing
            ? "텍스트를 인식하지 못해 표시할 수 없습니다."
            : "임베딩이 완료되면 표시됩니다."}
        </p>
      ) : response.items.length === 0 ? (
        <p className="text-sm text-muted">추천할 태그가 없습니다.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {response.items.map((item) => {
            const content = (
              <>
                <span>{item.tag}</span>
                <span className="text-muted">{item.freq}</span>
              </>
            );
            return (
              <li key={item.tag}>
                {onApply === undefined ? (
                  <span className="flex items-center gap-2 rounded bg-sunken px-3 py-1.5 text-xs text-body">
                    {content}
                  </span>
                ) : (
                  <button
                    aria-label={`${item.tag} 태그 적용`}
                    className="flex items-center gap-2 rounded bg-sunken px-3 py-1.5 text-xs text-body hover:text-strong"
                    onClick={() => void onApply(item.tag)}
                    type="button"
                  >
                    {content}
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
