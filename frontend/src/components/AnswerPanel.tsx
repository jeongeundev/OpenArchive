"use client";

import Link from "next/link";

import type { AnswerSource, AskResponse } from "@/lib/types";

/** 인용이 가리키는 「그 버전의 그 자리」 — 문서 상세가 이 주소로 그 판을 펼치고 대목을 강조한다. */
function sourceHref(source: AnswerSource): string {
  return `/documents/${encodeURIComponent(source.document_id)}?version=${source.based_on_version}&chunk=${source.chunk_index}`;
}

function versionLabel(source: AnswerSource): string {
  return source.revised
    ? `v${source.based_on_version} 기준 · 현재 v${source.current_version}`
    : `v${source.based_on_version} 기준`;
}

/** 답의 `[번호]`를 근거 링크로 바꾼다. 근거에 없는 번호는 모델이 지어낸 것이라 글자 그대로 둔다. */
function AnswerText({ answer, sources }: { answer: string; sources: AnswerSource[] }): React.ReactElement {
  const byLabel = new Map(sources.map((source) => [source.label, source]));
  const parts = answer.split(/(\[\d+\])/);
  return (
    <p data-testid="answer-text" className="whitespace-pre-wrap text-sm leading-relaxed text-neutral-200">
      {parts.map((part, index) => {
        const source = byLabel.get(Number(/^\[(\d+)\]$/.exec(part)?.[1]));
        return source === undefined ? (
          part
        ) : (
          <Link key={index} href={sourceHref(source)} title={`${source.title} · ${versionLabel(source)}`}
            className="text-[#0ea5e9] hover:underline">
            {part}
          </Link>
        );
      })}
    </p>
  );
}

const UNANSWERED: Record<Exclude<AskResponse["status"], "answered">, string> = {
  disabled:
    "답변 생성이 설정되지 않았습니다. 관리자가 ANSWER_PROVIDER=ollama로 켤 수 있습니다. 검색 결과는 그대로 쓸 수 있습니다.",
  no_evidence: "근거로 쓸 문서를 찾지 못했습니다.",
  failed: "검색 결과는 아래에 그대로 있습니다.",
};

export function AnswerPanel({
  response,
  loading,
  error,
  onAsk,
}: {
  response: AskResponse | null;
  loading: boolean;
  error: string | null;
  onAsk: () => void;
}): React.ReactElement {
  const cited = response?.sources.filter((source) => source.cited) ?? [];
  const uncited = response?.sources.filter((source) => !source.cited) ?? [];

  return (
    <section aria-labelledby="answer-panel" className="space-y-4 rounded-lg border border-neutral-800 bg-[#141414] p-6">
      <div>
        <h2 id="answer-panel" className="font-medium text-white">근거 기반 답변</h2>
        <p className="mt-2 text-xs text-neutral-500">
          검색된 문서만 근거로 답하도록 지시합니다. 보장은 아닙니다 — 인용한 근거를 확인하세요.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-neutral-400">답변을 만드는 중… 로컬 모델은 수십 초 걸릴 수 있습니다.</p>
      ) : null}
      {error !== null ? <p className="text-sm text-[#ef4444]" role="status">{error}</p> : null}

      {response === null && !loading ? (
        <button type="button" onClick={onAsk}
          className="rounded-lg border border-neutral-700 px-4 py-2 text-sm text-white hover:border-[#0ea5e9]">
          이 검색어로 답변 받기
        </button>
      ) : null}

      {response !== null && response.status !== "answered" ? (
        <p className="text-sm text-neutral-400">
          {response.status === "failed" && response.detail !== null ? `${response.detail} ` : ""}
          {UNANSWERED[response.status]}
        </p>
      ) : null}

      {response?.status === "answered" && response.answer !== null ? (
        <>
          <AnswerText answer={response.answer} sources={response.sources} />
          {cited.length > 0 ? (
            <ol aria-label="인용한 근거" className="space-y-3 border-t border-neutral-800 pt-4">
              {cited.map((source) => (
                <li key={source.label} className="text-sm">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-neutral-500">[{source.label}]</span>
                    <Link href={sourceHref(source)} className="text-white hover:text-[#0ea5e9]">
                      {source.title}
                    </Link>
                    <span className={source.revised ? "text-xs text-neutral-300" : "text-xs text-neutral-500"}>
                      {versionLabel(source)}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-xs text-neutral-400">{source.content}</p>
                </li>
              ))}
            </ol>
          ) : null}
          {uncited.length > 0 ? (
            <details className="text-sm">
              <summary className="cursor-pointer text-neutral-500">인용하지 않은 근거 {uncited.length}건</summary>
              <ul className="mt-2 space-y-1">
                {uncited.map((source) => (
                  <li key={source.label}>
                    <Link href={sourceHref(source)} className="text-neutral-400 hover:text-[#0ea5e9]">
                      [{source.label}] {source.title}
                    </Link>
                    <span className="ml-2 text-xs text-neutral-500">{versionLabel(source)}</span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
