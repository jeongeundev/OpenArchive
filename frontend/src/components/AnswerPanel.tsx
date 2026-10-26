// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";

import { passageHref } from "@/lib/passageLink";
import type { AnswerSource, AskResponse } from "@/lib/types";

function versionLabel(source: AnswerSource): string {
  return source.revised
    ? `v${source.based_on_version} 기준 · 현재 v${source.current_version}`
    : `v${source.based_on_version} 기준`;
}

/** 검색 결과의 메타 줄과 같은 규칙 — 빈 값은 빼고 ` · `로 잇는다. */
function sourceMeta(parts: (string | null | undefined)[]): string {
  return parts.filter(Boolean).join(" · ");
}

/** 답의 `[번호]`를 근거 링크로 바꾼다. 근거에 없는 번호는 모델이 지어낸 것이라 글자 그대로 둔다. */
function AnswerText({ answer, sources }: { answer: string; sources: AnswerSource[] }): React.ReactElement {
  const byLabel = new Map(sources.map((source) => [source.label, source]));
  const parts = answer.split(/(\[\d+\])/);
  return (
    <p data-testid="answer-text" className="whitespace-pre-wrap text-sm leading-relaxed text-strong">
      {parts.map((part, index) => {
        const source = byLabel.get(Number(/^\[(\d+)\]$/.exec(part)?.[1]));
        return source === undefined ? (
          part
        ) : (
          <Link key={index} href={passageHref(source)} title={`${source.title} · ${versionLabel(source)}`}
            className="text-accent hover:underline">
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
    <section aria-labelledby="answer-panel" className="space-y-4 rounded-lg border border-line bg-card p-6">
      <div>
        <h2 id="answer-panel" className="font-medium text-strong">근거 기반 답변</h2>
        <p className="mt-2 text-xs text-muted">
          검색된 문서만 근거로 답하도록 지시합니다. 보장은 아닙니다 — 인용한 근거를 확인하세요.
        </p>
      </div>

      {loading ? (
        <p className="text-sm text-muted">답변을 만드는 중… 로컬 모델은 수십 초 걸릴 수 있습니다.</p>
      ) : null}
      {error !== null ? <p className="text-sm text-failed" role="status">{error}</p> : null}

      {response === null && !loading ? (
        <button type="button" onClick={onAsk}
          className="rounded-lg border border-line-strong px-4 py-2 text-sm text-strong hover:border-accent">
          이 검색어로 답변 받기
        </button>
      ) : null}

      {response !== null && response.status !== "answered" ? (
        <p className="text-sm text-muted">
          {response.status === "failed" && response.detail !== null ? `${response.detail} ` : ""}
          {UNANSWERED[response.status]}
        </p>
      ) : null}

      {response?.status === "answered" && response.answer !== null ? (
        <>
          <AnswerText answer={response.answer} sources={response.sources} />
          {cited.length > 0 ? (
            <div className="space-y-3 border-t border-line pt-4">
            <h3 id="answer-cited" className="text-sm font-medium text-strong">인용한 근거</h3>
            <ol aria-labelledby="answer-cited" className="space-y-3">
              {cited.map((source) => (
                <li key={source.label} className="text-sm">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-muted">[{source.label}]</span>
                    <Link href={passageHref(source)} className="text-strong hover:text-accent">
                      {source.title}
                    </Link>
                    <span className={source.revised ? "text-xs text-body" : "text-xs text-muted"}>
                      {versionLabel(source)}
                    </span>
                  </div>
                  <p className="mt-1 break-words text-xs text-muted">
                    {sourceMeta([source.content_type?.toUpperCase(), source.folder_path?.join(" › "), source.owner])}
                  </p>
                  <p className="mt-1 line-clamp-3 whitespace-pre-wrap text-xs text-muted">{source.content}</p>
                </li>
              ))}
            </ol>
            </div>
          ) : null}
          {uncited.length > 0 ? (
            <details className="text-sm">
              <summary className="cursor-pointer text-muted">인용하지 않은 근거 {uncited.length}건</summary>
              <ul className="mt-2 space-y-1">
                {uncited.map((source) => (
                  <li key={source.label}>
                    <Link href={passageHref(source)} className="text-muted hover:text-accent">
                      [{source.label}] {source.title}
                    </Link>
                    <span className="ml-2 text-xs text-muted">{versionLabel(source)}</span>
                    <span className="ml-2 text-xs text-muted">
                      {sourceMeta([source.content_type?.toUpperCase(), source.owner])}
                    </span>
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
