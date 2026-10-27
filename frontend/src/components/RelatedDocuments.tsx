// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import Link from "next/link";

import { relationLabel } from "@/lib/relations";
import type { RelatedResponse } from "@/lib/types";

// textMissing: 텍스트 인식에 실패해 임베딩할 텍스트가 없는 문서다 (ADR-052). 색인 전 안내가
// 오지 않을 완료를 약속하지 않게 한다.
export function RelatedDocuments({
  response,
  textMissing = false,
}: {
  response: RelatedResponse;
  textMissing?: boolean;
}): React.ReactElement {
  const groups = Array.from(
    response.items.reduce((byKind, item) => {
      const items = byKind.get(item.kind) ?? [];
      items.push(item);
      byKind.set(item.kind, items);
      return byKind;
    }, new Map<string, RelatedResponse["items"]>()),
  );

  return (
    <section className="space-y-4 rounded-xl border border-line bg-card p-5">
      <h2 className="text-sm font-semibold text-strong">관련 문서</h2>
      {response.reason === "not_indexed" ? (
        <p className="text-sm text-muted">
          {textMissing
            ? "텍스트를 인식하지 못해 표시할 수 없습니다."
            : "임베딩이 완료되면 표시됩니다."}
        </p>
      ) : (
        <>
          {response.identical.length > 0 ? (
            <div className="space-y-2">
              <p className="text-sm text-body">동일한 텍스트의 문서가 있습니다</p>
              <ul className="space-y-1">
                {response.identical.map((item) => (
                  <li key={item.document_id}>
                    <Link
                      href={`/documents/${item.document_id}`}
                      className="text-sm text-body hover:text-accent"
                    >
                      {item.title}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {response.reason === "no_edges" ? (
            <p className="text-sm text-muted">처리는 끝났지만 관련 문서가 없습니다.</p>
          ) : null}
        </>
      )}
      {response.reason !== "not_indexed" && response.items.length > 0 ? (
        <div className="space-y-4">
          <p className="text-sm text-body">내용이 유사한 문서가 있습니다</p>
          {groups.map(([kind, items]) => (
            <div key={kind} className="space-y-2">
              <h3 className="text-xs font-medium text-muted">{relationLabel(kind)}</h3>
              {/* 검색 화면 「함께 볼 문서」 카드와 같은 모양 — 관계 라벨 · 점수, 그 아래 제목. */}
              <ul className="space-y-2">
                {items.map((item) => (
                  <li key={item.document_id} className="space-y-2 rounded-xl border border-line bg-card p-4">
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                      <span>{relationLabel(kind)}</span>
                      <span className="text-accent">{item.score.toFixed(3)}</span>
                      <span>v{response.based_on_version} 기준</span>
                      {item.mutual ? <span>서로 찾음</span> : null}
                    </div>
                    <Link
                      href={`/documents/${item.document_id}`}
                      className="block break-words text-sm font-semibold text-strong hover:text-accent"
                    >
                      {item.title}
                    </Link>
                    <div className="flex flex-wrap gap-2">
                      {item.tags.map((tag) => (
                        <span key={tag} className="rounded bg-sunken px-2 py-1 text-xs text-body">
                          {tag}
                        </span>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
