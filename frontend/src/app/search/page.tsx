// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { AnswerPanel } from "@/components/AnswerPanel";
import { useAuth } from "@/components/AuthProvider";
import { SearchForm } from "@/components/SearchForm";
import { SearchResults } from "@/components/SearchResults";
import type { Folder } from "@/lib/types";
import { useAsk } from "@/lib/useAsk";
import { useFolders } from "@/lib/useFolders";
import { useSearch, type SearchInput } from "@/lib/useSearch";
import { searchHref, searchInputFromParams } from "@/lib/urlState";

export default function SearchPage(): React.ReactElement {
  // 검색 조건을 주소에 두므로 useSearchParams가 필요하고, 정적 export는 Suspense 경계를 요구한다.
  return (
    <Suspense fallback={<p className="text-sm text-muted">불러오는 중…</p>}>
      <SearchRoute />
    </Suspense>
  );
}

/**
 * 검색 조건은 주소가 기준이다 — 문서로 갔다가 뒤로 오면 같은 조건으로 다시 검색한다.
 * 답변은 다시 만들지 않는다(수십 초 걸린다). 주소가 바뀌면 화면을 새로 그려 이전 결과·답을 버린다.
 */
function SearchRoute(): React.ReactElement {
  const router = useRouter();
  const query = useSearchParams()?.toString() ?? "";
  const requested = useMemo(() => searchInputFromParams(new URLSearchParams(query)), [query]);
  const { auth, loading: authLoading } = useAuth();
  // 공유 링크 열람자는 /api/folders가 403이다 — 목록이 비면 폴더 칸을 그리지 않고 오류도 띄우지 않는다.
  const { folders, loading: foldersLoading } = useFolders(auth.authenticated);
  // 같은 조건으로 다시 제출하면 주소가 그대로라 이 값으로 새로 그린다.
  const [round, setRound] = useState(0);

  // 주소의 폴더는 볼 수 있는 폴더 목록이 온 뒤에 판정한다. 볼 수 없으면 폼이 「전체 폴더」로 보이므로 검색에서도 뺀다.
  const settled = requested?.folderId == null || (!authLoading && !foldersLoading);
  const visibleFolder = folders.some((folder) => folder.id === requested?.folderId);
  const input = requested === null || !settled ? null
    : requested.folderId !== null && !visibleFolder ? { ...requested, folderId: null } : requested;

  function search(next: SearchInput): void {
    const href = searchHref(next);
    if (requested !== null && href === searchHref(requested)) setRound((value) => value + 1);
    else router.push(href);
  }

  return (
    <SearchView
      key={`${query}#${round}`}
      initial={requested}
      input={input}
      folders={folders}
      authenticated={auth.authenticated}
      onSearch={search}
    />
  );
}

function SearchView({ initial, input, folders, authenticated, onSearch }: {
  initial: SearchInput | null;
  input: SearchInput | null;
  folders: Folder[];
  authenticated: boolean;
  onSearch: (input: SearchInput) => void;
}): React.ReactElement {
  const { response, loading, error, run } = useSearch();
  const answer = useAsk();
  // 폴더 목록이 다시 와도 같은 조건이면 다시 검색하지 않는다.
  const inputKey = input === null ? null : JSON.stringify(input);

  useEffect(() => {
    if (inputKey !== null) run(JSON.parse(inputKey) as SearchInput);
  }, [inputKey, run]);

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-strong">검색</h1>
        <p className="mt-3 text-sm text-muted">
          태그·유형·폴더 필터와 벡터 유사도를 한 번의 SQL로 검색합니다.
        </p>
      </div>

      <SearchForm onSearch={onSearch} pending={loading} folders={folders} initial={initial} />
      {/* /api/ask는 로그인한 사용자만 쓴다 — 익명에게 버튼을 보이면 누르는 순간 401이다.
          답변은 화면에 보이는 검색 결과와 같은 입력으로 묻는다 — 폼이 바뀌어도 마지막 검색 기준이다. */}
      {authenticated && input !== null ? (
        <AnswerPanel
          response={answer.response}
          loading={answer.loading}
          error={answer.error}
          onAsk={() => answer.run(input)}
        />
      ) : null}
      <SearchResults response={response} loading={loading} error={error} />
    </section>
  );
}
