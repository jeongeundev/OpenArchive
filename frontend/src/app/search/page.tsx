"use client";

import { SearchForm } from "@/components/SearchForm";
import { SearchResults } from "@/components/SearchResults";
import { useSearch } from "@/lib/useSearch";

export default function SearchPage(): React.ReactElement {
  const { response, loading, error, run } = useSearch();

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-white">검색</h1>
        <p className="mt-3 text-sm text-neutral-400">
          태그·유형 필터와 벡터 유사도를 한 번의 SQL로 검색합니다.
        </p>
      </div>

      <SearchForm onSearch={run} pending={loading} />
      <SearchResults response={response} loading={loading} error={error} />
    </section>
  );
}
