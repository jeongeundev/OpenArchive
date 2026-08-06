import type { TagSuggestionsResponse } from "@/lib/types";

export function TagSuggestions({
  response,
  onApply,
}: {
  response: TagSuggestionsResponse;
  onApply?: (tag: string) => void | Promise<void>;
}): React.ReactElement {
  return (
    <section className="space-y-4">
      <h2 className="text-sm font-medium text-neutral-400">태그 추천</h2>
      {response.reason === "not_indexed" ? (
        <p className="text-sm text-neutral-500">임베딩이 완료되면 표시됩니다.</p>
      ) : response.items.length === 0 ? (
        <p className="text-sm text-neutral-500">추천할 태그가 없습니다.</p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {response.items.map((item) => {
            const content = (
              <>
                <span>{item.tag}</span>
                <span className="text-neutral-500">{item.freq}</span>
              </>
            );
            return (
              <li key={item.tag}>
                {onApply === undefined ? (
                  <span className="flex items-center gap-2 rounded bg-neutral-800 px-3 py-1.5 text-xs text-neutral-300">
                    {content}
                  </span>
                ) : (
                  <button
                    aria-label={`${item.tag} 태그 적용`}
                    className="flex items-center gap-2 rounded bg-neutral-800 px-3 py-1.5 text-xs text-neutral-300 hover:text-white"
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
