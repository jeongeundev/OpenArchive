// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ApiError, getClusters } from "@/lib/api";
import type { Cluster, ClustersResponse } from "@/lib/types";

const WIDTH = 900;
const HEIGHT = 600;
// 이름을 원 아래에 두므로 맨 아래 원(반지름 최대 44) 밑에 글자 한 줄이 들어갈 자리를 남긴다.
const LAYOUT_RADIUS = 200;
const NAME_LIMIT = 24;

interface Position {
  x: number;
  y: number;
}

// 최소~최대 범위 안의 위치(0~1). 최대값만 기준으로 삼으면 값이 균질할 때 대비가 사라진다(#89 ②).
// 값이 모두 같으면 1 — 차이가 없다는 것 자체가 정보다.
function spread(value: number, values: number[]): number {
  const smallest = Math.min(...values);
  const largest = Math.max(...values);
  return largest === smallest ? 1 : (value - smallest) / (largest - smallest);
}

// 긴 이름이 이웃 덩어리 이름과 겹치지 않게 줄인다. 전체 이름은 title·aria-label에 남는다.
function shortName(name: string): string {
  const chars = Array.from(name);
  return chars.length > NAME_LIMIT ? `${chars.slice(0, NAME_LIMIT).join("")}…` : name;
}

export default function ClustersPage(): React.ReactElement {
  const [data, setData] = useState<ClustersResponse | null>(null);
  const [selected, setSelected] = useState<Cluster | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getClusters(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof ApiError ? reason.detail : "관계 지도를 불러오지 못했습니다.");
      });
    return () => controller.abort();
  }, []);

  const positions = useMemo(() => {
    const result = new Map<string, Position>();
    const count = data?.clusters.length ?? 0;
    data?.clusters.forEach((cluster, index) => {
      // 둘이면 위아래가 아니라 좌우로 — 가로로 넓은 화면을 쓰고 이름이 아래 원과 부딪히지 않는다.
      if (count === 2) {
        result.set(cluster.name, { x: WIDTH / 2 + (index === 0 ? -LAYOUT_RADIUS : LAYOUT_RADIUS), y: HEIGHT / 2 });
        return;
      }
      const angle = -Math.PI / 2 + (2 * Math.PI * index) / Math.max(count, 1);
      result.set(cluster.name, {
        x: WIDTH / 2 + LAYOUT_RADIUS * Math.cos(angle),
        y: HEIGHT / 2 + LAYOUT_RADIUS * Math.sin(angle),
      });
    });
    return result;
  }, [data]);

  const sizes = data?.clusters.map((cluster) => cluster.size) ?? [];
  const counts = data?.connections.map((connection) => connection.count) ?? [];

  return (
    <div className="space-y-8">
      <header>
        <p className="text-sm text-muted">현재 열람 범위 기준</p>
        <h1 className="mt-2 text-4xl font-semibold text-strong">관계 지도</h1>
        <p className="mt-3 text-sm text-muted">
          관계 그래프에서 서로 많이 이어진 문서끼리 묶었습니다. 원의 크기는 문서 수, 선의 굵기는 덩어리 사이 관계 수이며, 가장 작은 값과 가장 큰 값 사이의 상대 크기로 그립니다. 덩어리 이름은 그 덩어리에 집중된 태그이고, 그런 태그가 없으면 연결이 가장 많은 문서의 제목입니다. 묶음은 관계로 계산한 추천이며 사실처럼 단정하지 않습니다.
        </p>
      </header>

      {error !== null ? <p className="text-sm text-failed" role="alert">{error}</p> : null}
      {data === null && error === null ? <p className="text-sm text-muted">불러오는 중…</p> : null}
      {data?.clusters.length === 0 ? <p className="text-sm text-muted">표시할 문서가 없습니다.</p> : null}
      {data !== null && data.clusters.length > 0 ? (
        <section className="overflow-x-auto rounded-lg border border-line bg-card p-4" aria-label="관계 지도 그래프">
          <svg className="max-h-[560px] w-full min-w-[720px]" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="문서 덩어리와 덩어리 사이 연결">
            {data.connections.map((connection) => {
              const source = positions.get(connection.source);
              const target = positions.get(connection.target);
              if (source === undefined || target === undefined) return null;
              return (
                <line
                  key={`${connection.source}:${connection.target}`}
                  className="stroke-line-strong"
                  strokeWidth={1 + 5 * spread(connection.count, counts)}
                  x1={source.x}
                  x2={target.x}
                  y1={source.y}
                  y2={target.y}
                />
              );
            })}
            {data.clusters.map((cluster) => {
              const position = positions.get(cluster.name)!;
              const radius = 22 + 22 * Math.sqrt(spread(cluster.size, sizes));
              return (
                <g
                  aria-label={`${cluster.name} 덩어리`}
                  className="cursor-pointer outline-none focus:[&_circle]:stroke-strong"
                  key={cluster.name}
                  onClick={() => setSelected(cluster)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") setSelected(cluster);
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <circle
                    className={selected?.name === cluster.name ? "fill-accent-soft stroke-accent" : "fill-card stroke-line-strong"}
                    cx={position.x}
                    cy={position.y}
                    r={radius}
                    strokeWidth="1.5"
                  />
                  <title>{cluster.name}</title>
                  <text className="pointer-events-none fill-muted text-xs" dominantBaseline="middle" textAnchor="middle" x={position.x} y={position.y}>{cluster.size}개</text>
                  <text className="pointer-events-none fill-strong text-sm font-medium" textAnchor="middle" x={position.x} y={position.y + radius + 20}>{shortName(cluster.name)}</text>
                </g>
              );
            })}
          </svg>
        </section>
      ) : null}

      {selected !== null ? (
        <section className="rounded-lg border border-line bg-card p-6">
          <h2 className="text-sm font-medium text-muted">{selected.name} 문서 {selected.size}개</h2>
          {selected.name === "미분류" ? (
            <p className="mt-2 text-sm text-muted">아직 관계가 계산되지 않았거나 이어진 문서가 없는 문서입니다.</p>
          ) : null}
          <ul className="mt-4 space-y-2">
            {selected.documents.map((document) => (
              <li key={document.document_id}>
                <Link className="text-sm text-body hover:text-accent" href={`/documents/${document.document_id}`}>
                  {document.title}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
