"use client";

import { useState } from "react";

import { AnswerPanel } from "@/components/AnswerPanel";
import { useAuth } from "@/components/AuthProvider";
import { SearchForm } from "@/components/SearchForm";
import { SearchResults } from "@/components/SearchResults";
import { useAsk } from "@/lib/useAsk";
import { useSearch, type SearchInput } from "@/lib/useSearch";

export default function SearchPage(): React.ReactElement {
  const { response, loading, error, run } = useSearch();
  const answer = useAsk();
  const { auth } = useAuth();
  // 답변은 화면에 보이는 검색 결과와 같은 입력으로 묻는다 — 폼이 바뀌어도 마지막 검색 기준이다.
  const [lastInput, setLastInput] = useState<SearchInput | null>(null);

  function search(input: SearchInput): void {
    setLastInput(input);
    answer.reset();
    run(input);
  }

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-white">검색</h1>
        <p className="mt-3 text-sm text-neutral-400">
          태그·유형 필터와 벡터 유사도를 한 번의 SQL로 검색합니다.
        </p>
      </div>

      <SearchForm onSearch={search} pending={loading} />
      {/* /api/ask는 로그인한 사용자만 쓴다 — 익명에게 버튼을 보이면 누르는 순간 401이다. */}
      {auth.authenticated && lastInput !== null ? (
        <AnswerPanel
          response={answer.response}
          loading={answer.loading}
          error={answer.error}
          onAsk={() => answer.run(lastInput)}
        />
      ) : null}
      <SearchResults response={response} loading={loading} error={error} />
    </section>
  );
}
