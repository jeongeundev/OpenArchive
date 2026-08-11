"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ApiError, getClusters } from "@/lib/api";
import type { Cluster, ClustersResponse } from "@/lib/types";

const WIDTH = 900;
const HEIGHT = 600;
const LAYOUT_RADIUS = 225;

interface Position {
  x: number;
  y: number;
}

function nodeRadius(size: number, largest: number): number {
  return 22 + 22 * Math.sqrt(size / largest);
}

export default function ClustersPage(): React.ReactElement {
  const [data, setData] = useState<ClustersResponse | null>(null);
  const [selected, setSelected] = useState<Cluster | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getClusters()
      .then((result) => {
        if (active) setData(result);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof ApiError ? reason.detail : "주제 덩어리를 불러오지 못했습니다.");
      });
    return () => {
      active = false;
    };
  }, []);

  const positions = useMemo(() => {
    const result = new Map<string, Position>();
    const count = data?.clusters.length ?? 0;
    data?.clusters.forEach((cluster, index) => {
      const angle = -Math.PI / 2 + (2 * Math.PI * index) / Math.max(count, 1);
      result.set(cluster.name, {
        x: WIDTH / 2 + LAYOUT_RADIUS * Math.cos(angle),
        y: HEIGHT / 2 + LAYOUT_RADIUS * Math.sin(angle),
      });
    });
    return result;
  }, [data]);

  const largest = Math.max(1, ...(data?.clusters.map((cluster) => cluster.size) ?? []));
  const strongest = Math.max(1, ...(data?.connections.map((connection) => connection.count) ?? []));

  return (
    <div className="space-y-8">
      <header>
        <p className="text-sm text-neutral-500">현재 열람 범위 기준</p>
        <h1 className="mt-2 text-4xl font-semibold text-white">주제 덩어리</h1>
        <p className="mt-3 text-sm text-neutral-400">원의 크기는 문서 수, 선의 굵기는 주제 사이 관계 수를 나타냅니다.</p>
      </header>

      {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
      {data === null && error === null ? <p className="text-sm text-neutral-500">불러오는 중…</p> : null}
      {data?.clusters.length === 0 ? <p className="text-sm text-neutral-500">표시할 문서가 없습니다.</p> : null}
      {data !== null && data.clusters.length > 0 ? (
        <section className="overflow-x-auto rounded-lg border border-neutral-800 bg-[#141414] p-4" aria-label="주제 덩어리 그래프">
          <svg className="min-w-[720px]" viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label="문서 주제 덩어리와 연결">
            {data.connections.map((connection) => {
              const source = positions.get(connection.source);
              const target = positions.get(connection.target);
              if (source === undefined || target === undefined) return null;
              return (
                <line
                  key={`${connection.source}:${connection.target}`}
                  stroke="#404040"
                  strokeWidth={1 + (5 * connection.count) / strongest}
                  x1={source.x}
                  x2={target.x}
                  y1={source.y}
                  y2={target.y}
                />
              );
            })}
            {data.clusters.map((cluster) => {
              const position = positions.get(cluster.name)!;
              const radius = nodeRadius(cluster.size, largest);
              return (
                <g
                  aria-label={`${cluster.name} 덩어리`}
                  className="cursor-pointer outline-none focus:[&_circle]:stroke-white"
                  key={cluster.name}
                  onClick={() => setSelected(cluster)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") setSelected(cluster);
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <circle
                    cx={position.x}
                    cy={position.y}
                    fill={selected?.name === cluster.name ? "#0ea5e9" : "#262626"}
                    r={radius}
                    stroke="#737373"
                    strokeWidth="1.5"
                  />
                  <text className="pointer-events-none fill-white text-sm font-medium" textAnchor="middle" x={position.x} y={position.y - 2}>{cluster.name}</text>
                  <text className="pointer-events-none fill-neutral-400 text-xs" textAnchor="middle" x={position.x} y={position.y + 16}>{cluster.size}개</text>
                </g>
              );
            })}
          </svg>
        </section>
      ) : null}

      {selected !== null ? (
        <section className="rounded-lg border border-neutral-800 bg-[#141414] p-6">
          <h2 className="text-sm font-medium text-neutral-400">{selected.name} 문서 {selected.size}개</h2>
          <ul className="mt-4 space-y-2">
            {selected.documents.map((document) => (
              <li key={document.document_id}>
                <Link className="text-sm text-neutral-300 hover:text-[#0ea5e9]" href={`/documents/${document.document_id}`}>
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
