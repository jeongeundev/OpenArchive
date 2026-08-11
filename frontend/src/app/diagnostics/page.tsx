"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, getDiagnostics } from "@/lib/api";
import type {
  DiagnosticDocumentList,
  DiagnosticsResponse,
  DuplicateList,
} from "@/lib/types";

function Empty(): React.ReactElement {
  return <p className="text-sm text-neutral-500">정리할 것이 없습니다</p>;
}

function DocumentList({ result }: { result: DiagnosticDocumentList }): React.ReactElement {
  if (result.items.length === 0) return <Empty />;
  return (
    <ul className="space-y-2">
      {result.items.map((document) => (
        <li key={document.document_id}>
          <Link className="text-sm text-neutral-300 hover:text-[#0ea5e9]" href={`/documents/${document.document_id}`}>
            {document.title}
          </Link>
        </li>
      ))}
    </ul>
  );
}

function DuplicatePairs({ result }: { result: DuplicateList }): React.ReactElement {
  if (result.items.length === 0) return <Empty />;
  return (
    <ul className="space-y-3">
      {result.items.map((pair) => (
        <li className="flex flex-wrap items-center gap-2 text-sm" key={`${pair.first.document_id}:${pair.second.document_id}`}>
          <Link className="text-neutral-300 hover:text-[#0ea5e9]" href={`/documents/${pair.first.document_id}`}>{pair.first.title}</Link>
          <span className="text-neutral-600">↔</span>
          <Link className="text-neutral-300 hover:text-[#0ea5e9]" href={`/documents/${pair.second.document_id}`}>{pair.second.title}</Link>
          {pair.score !== null ? <span className="text-neutral-500">닿은 대목 {Math.round(pair.score * 100)}%</span> : null}
        </li>
      ))}
    </ul>
  );
}

export default function DiagnosticsPage(): React.ReactElement {
  const [diagnostics, setDiagnostics] = useState<DiagnosticsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getDiagnostics()
      .then((result) => {
        if (active) setDiagnostics(result);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof ApiError ? reason.detail : "진단 결과를 불러오지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="space-y-8">
      <header>
        <p className="text-sm text-neutral-500">현재 열람 범위 기준</p>
        <h1 className="mt-2 text-4xl font-semibold text-white">문서 진단</h1>
        <p className="mt-3 text-sm text-neutral-400">문서 연결과 분류 상태를 살펴보고 정리할 후보를 찾습니다.</p>
      </header>

      {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
      {diagnostics === null && error === null ? <p className="text-sm text-neutral-500">불러오는 중…</p> : null}
      {diagnostics !== null ? (
        <div className="grid gap-4 md:grid-cols-3">
          <section className="rounded-lg border border-neutral-800 bg-[#141414] p-6">
            <h2 className="text-sm font-medium text-neutral-400">연결 없는 문서 <span className="text-white">{diagnostics.orphans.count}</span></h2>
            <p className="my-4 text-sm text-neutral-500">관련 문서가 없습니다. 태그를 달거나 다른 문서에서 참조해 보세요.</p>
            <DocumentList result={diagnostics.orphans} />
          </section>

          <section className="rounded-lg border border-neutral-800 bg-[#141414] p-6">
            <h2 className="text-sm font-medium text-neutral-400">같거나 가까운 문서 <span className="text-white">{diagnostics.duplicates.identical.count + diagnostics.duplicates.overlaps.count}</span></h2>
            <p className="my-4 text-sm text-neutral-500">동일 텍스트는 하나만 남겨도 됩니다. 아래 목록은 같은 내용이라는 뜻이 아니라, 여러 대목이 서로 가장 가깝다는 뜻입니다.</p>
            <h3 className="mb-2 text-xs font-medium text-neutral-500">동일 텍스트 {diagnostics.duplicates.identical.count}</h3>
            <DuplicatePairs result={diagnostics.duplicates.identical} />
            <h3 className="mb-2 mt-5 text-xs font-medium text-neutral-500">여러 대목에서 만남 {diagnostics.duplicates.overlaps.count}</h3>
            <DuplicatePairs result={diagnostics.duplicates.overlaps} />
          </section>

          <section className="rounded-lg border border-neutral-800 bg-[#141414] p-6">
            <h2 className="text-sm font-medium text-neutral-400">태그 없는 문서 <span className="text-white">{diagnostics.uncategorized.count}</span></h2>
            <p className="my-4 text-sm text-neutral-500">태그를 달아 검색과 탐색에서 분류 기준을 더해 보세요.</p>
            <DocumentList result={diagnostics.uncategorized} />
          </section>
        </div>
      ) : null}
    </div>
  );
}
