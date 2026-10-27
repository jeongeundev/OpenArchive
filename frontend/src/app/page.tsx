// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { DocumentStatusBadge } from "@/components/StatusBadge";
import { OwnerLabel } from "@/components/OwnerLabel";
import { countDocuments, getDocument, getDocumentProgress, listDocuments, listUnfiled } from "@/lib/api";
import { useFolders } from "@/lib/useFolders";
import { searchHref } from "@/lib/urlState";

const card = "min-w-0 rounded-xl border border-line bg-card p-5";
const loadCount = (signal: AbortSignal) => countDocuments(undefined, signal);
const loadRecent = (signal: AbortSignal) => listDocuments({ sort: "updated", limit: 4 }, signal);
const loadUnfiled = async (signal: AbortSignal) => {
  const result = await listUnfiled(signal);
  const items = await Promise.all(result.items.slice(0, 3).map(async item => ({
    ...item,
    detail: await getDocument(item.document_id, signal).catch(() => null),
  })));
  return { ...result, items };
};

function useHomeData<T>(load: (signal: AbortSignal) => Promise<T>, enabled: boolean) {
  const [state, setState] = useState<{ data?: T; error?: string }>({});
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void load(controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ data });
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setState({ error: reason instanceof Error ? reason.message : "불러오지 못했습니다." });
    });
    return () => controller.abort();
  }, [load, enabled]);
  return state;
}

function Loading() {
  return <span aria-label="불러오는 중" className="inline-block h-7 w-20 rounded bg-sunken" />;
}

export default function Home(): React.ReactElement {
  const { auth } = useAuth();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const count = useHomeData(loadCount, auth.authenticated);
  const progress = useHomeData(getDocumentProgress, auth.authenticated);
  const recent = useHomeData(loadRecent, auth.authenticated);
  const unfiled = useHomeData(loadUnfiled, auth.authenticated);
  const { folders, loading: foldersLoading, error: foldersError } = useFolders(auth.authenticated);
  const roots = folders.filter(folder => folder.parent_id === null);
  const totals = roots.map(root => {
    const ids = new Set([root.id]);
    let size = 0;
    while (size !== ids.size) {
      size = ids.size;
      for (const folder of folders) if (folder.parent_id && ids.has(folder.parent_id)) ids.add(folder.id);
    }
    return { ...root, total: folders.filter(folder => ids.has(folder.id)).reduce((sum, folder) => sum + folder.document_count, 0) };
  });
  const maximum = Math.max(1, ...totals.map(folder => folder.total));
  const p = progress.data;
  const stats = [
    { label: "전체 문서", description: "열람 가능한 문서", value: count.data, error: count.error },
    { label: "검색 가능", description: "처리 완료", value: p ? `${p.ready} / ${p.extracting + p.extraction_failed + p.pending + p.processing + p.ready + p.error}` : undefined, error: progress.error },
    { label: "정리할 문서", description: "폴더 미지정", value: unfiled.data?.count, error: unfiled.error, href: "/organize" },
    { label: "부서", description: "부서 폴더", value: foldersLoading ? undefined : roots.filter(folder => folder.department).length, error: foldersError, href: "/departments" },
  ];
  return (
    <div className="min-w-0 space-y-8">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div><h1 className="text-3xl font-bold text-strong">안녕하세요, {auth.username}님</h1><p className="mt-2 text-muted">조직 문서를 올리고, 질문으로 찾아보세요.</p></div>
        <Link href="/documents?upload=1" className="rounded-lg bg-strong px-5 py-3 text-on-strong">문서 올리기</Link>
      </header>
      <section className="rounded-xl border border-accent/30 bg-accent-soft p-6">
        <h2 className="mb-4 font-semibold text-accent">AI 문서 검색</h2>
        <form className="flex flex-col gap-3 sm:flex-row" onSubmit={event => {
          event.preventDefault();
          if (query.trim()) router.push(searchHref({ query: query.trim(), tags: [], contentType: null, folderId: null, k: 10 }));
        }}>
          <input aria-label="AI 문서 검색" placeholder="무엇이든 물어보세요. 예) 장애 발생 시 대응 절차는?" value={query} onChange={event => setQuery(event.target.value)} className="min-w-0 flex-1 rounded-lg border border-line bg-card px-4 py-3 text-body" />
          <button className="rounded-lg bg-accent px-6 py-3 font-semibold text-on-strong">검색</button>
        </form>
      </section>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map(stat => {
          const contents = <><h2 className="text-sm text-muted">{stat.label}</h2><div className="my-3 text-3xl font-bold text-strong">{stat.error ? <p role="alert" className="text-sm font-normal text-failed">{stat.error}</p> : stat.value === undefined ? <Loading /> : stat.value}</div><p className="text-sm text-muted">{stat.description}</p></>;
          return stat.href ? <Link key={stat.label} href={stat.href} className={card}>{contents}</Link> : <section key={stat.label} className={card}>{contents}</section>;
        })}
      </div>
      <section aria-label="최근 문서">
        <div className="mb-4 flex items-center justify-between"><h2 className="text-xl font-bold text-strong">최근 문서</h2><Link href="/documents" className="text-sm text-accent">전체 보기</Link></div>
        {recent.error ? <p role="alert" className="text-failed">{recent.error}</p> : !recent.data ? <Loading /> : recent.data.length === 0 ? <p className="text-muted">문서가 없습니다.</p> : <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {recent.data.map(document => <article key={document.id} className={`relative ${card}`}>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2"><span className="rounded bg-sunken px-2 py-1 text-xs text-body">{document.content_type.toUpperCase()}</span><DocumentStatusBadge document={document} /></div>
            <Link href={`/documents/${document.id}`} className="line-clamp-2 break-words font-semibold text-strong after:absolute after:inset-0 hover:text-accent">{document.title}</Link>
            {document.folder_path?.length ? <p className="mt-3 break-words text-xs text-muted">{document.folder_path.join(" › ")}</p> : null}
            <div className="relative z-10 mt-2 w-fit text-xs text-muted"><OwnerLabel owner={document.owner_id} department={document.owner_department} /> · {new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(document.updated_at)).replace(". ", " ")}</div>
          </article>)}
        </div>}
      </section>
      <div className="grid gap-6 lg:grid-cols-2">
        <section className={card}>
          <div className="mb-5 flex items-center justify-between"><h2 className="text-lg font-bold text-strong">부서별 문서</h2><Link href="/departments" className="text-sm text-accent">부서 보기</Link></div>
          {foldersError ? <p role="alert" className="text-failed">{foldersError}</p> : foldersLoading ? <Loading /> : totals.length === 0 ? <p className="text-muted">볼 수 있는 폴더가 없습니다.</p> : <div className="space-y-5">{totals.map(folder => <div key={folder.id}>
            <div className="mb-2 flex justify-between gap-3 text-sm text-body"><span className="break-words">{folder.name}</span><span className="shrink-0">{folder.total}건</span></div>
            <div className="h-2 overflow-hidden rounded bg-sunken"><div className="h-full rounded bg-accent" style={{ width: `${folder.total / maximum * 100}%` }} /></div>
          </div>)}</div>}
        </section>
        <section className={card}>
          <div className="mb-5 flex items-center justify-between"><h2 className="text-lg font-bold text-strong">정리가 필요한 문서</h2>{unfiled.data && <span className="rounded-full bg-attention-soft px-2 py-1 text-xs text-attention">{unfiled.data.count}건</span>}</div>
          {unfiled.error ? <p role="alert" className="text-failed">{unfiled.error}</p> : !unfiled.data ? <Loading /> : unfiled.data.count === 0 ? <p className="text-sm text-muted">정리할 문서가 없습니다.</p> : <div className="space-y-3">{unfiled.data.items.map(item => <div key={item.document_id} className="flex flex-wrap items-center gap-3 rounded-lg bg-sunken p-3">
            {item.detail && <span className="text-xs text-muted">{item.detail.content_type.toUpperCase()}</span>}
            <div className="min-w-0 flex-1"><Link href={`/documents/${item.document_id}`} className="break-words text-sm font-medium text-strong">{item.title}</Link>{item.detail ? <div className="text-xs text-muted"><OwnerLabel owner={item.detail.owner_id} department={item.detail.owner_department} /></div> : <p className="text-xs text-failed">문서 정보를 불러오지 못했습니다.</p>}</div>
            <Link href="/organize" className="rounded-lg border border-line-strong bg-card px-3 py-2 text-xs text-body">폴더 지정</Link>
          </div>)}</div>}
          <div className="mt-5 text-sm text-muted">{progress.error ? <p role="alert" className="text-failed">{progress.error}</p> : p ? `처리 대기 ${p.pending + p.processing + p.extracting} · 처리 실패 ${p.error + p.extraction_failed}` : <Loading />}</div>
        </section>
      </div>
    </div>
  );
}
