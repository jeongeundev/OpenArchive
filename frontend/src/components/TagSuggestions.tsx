import type { TagSuggestionsResponse } from "@/lib/types";

export function TagSuggestions({
  response,
}: {
  response: TagSuggestionsResponse;
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
          {response.items.map((item) => (
            <li
              key={item.tag}
              className="flex items-center gap-2 rounded bg-neutral-800 px-3 py-1.5 text-xs text-neutral-300"
            >
              <span>{item.tag}</span>
              <span className="text-neutral-500">{item.freq}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
