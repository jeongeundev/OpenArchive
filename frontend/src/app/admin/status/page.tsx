// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useEffect, useRef, useState } from "react";
import { listOutages } from "@/lib/api";
import { OUTAGES_REFRESH_MS, STATUS_UNRESPONSIVE_MS } from "@/lib/systemHealth";
import type { DbOutage } from "@/lib/types";
import { ErrorDocuments } from "@/components/ErrorDocuments";
import { StatusPanel } from "@/components/StatusPanel";
import { useSystemStatus } from "@/lib/useSystemStatus";

export default function AdminStatusPage(): React.ReactElement {
  const { status, error, checkedAt, lastSuccessAt, refresh } = useSystemStatus();
  const [now, setNow] = useState(() => Date.now());
  const [startedAt] = useState(() => Date.now());
  const [outages, setOutages] = useState<DbOutage[]>([]);
  const [outagesError, setOutagesError] = useState<string | null>(null);
  const previousSuccess = useRef<number | null>(null);
  const [recovery, setRecovery] = useState(0);
  const successTime = lastSuccessAt?.getTime() ?? null;
  const elapsed = Math.max(0, now - (successTime ?? startedAt));

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setRecovery(value => value + 1), OUTAGES_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (successTime === null) return;
    const previous = previousSuccess.current ?? startedAt;
    previousSuccess.current = successTime;
    if (successTime - previous > STATUS_UNRESPONSIVE_MS) setRecovery(value => value + 1);
  }, [successTime, startedAt]);

  useEffect(() => {
    const controller = new AbortController();
    void listOutages(controller.signal).then(items => {
      if (!controller.signal.aborted) { setOutages(items); setOutagesError(null); }
    }).catch(() => {
      if (!controller.signal.aborted) setOutagesError("장애 기록을 불러오지 못했습니다.");
    });
    return () => controller.abort();
  }, [recovery]);
  return (
    <div className="space-y-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-sm font-semibold text-accent">관리 · 운영·데모 전용</p>
          <h1 className="text-4xl font-semibold text-strong">시스템 상태</h1>
          <p className="mt-3 text-sm text-muted">연결이 잠시 끊겨도 마지막 관측값을 유지하며, 짧은 중단 뒤 자동으로 복구됩니다.</p>
        </div>
        <div className="flex items-center gap-4">
          {checkedAt !== null && <p className="text-xs text-muted">마지막 확인 {checkedAt.toLocaleTimeString("ko-KR")}</p>}
          <button type="button" onClick={() => { refresh(); setRecovery(value => value + 1); }} className="rounded-lg border border-line-strong px-4 py-2 text-sm font-medium text-body hover:bg-sunken">새로고침</button>
        </div>
      </header>
      <StatusPanel status={status} error={error} outages={outages} outagesError={outagesError}
        lastSuccessAt={lastSuccessAt} unresponsiveSeconds={elapsed > STATUS_UNRESPONSIVE_MS ? Math.floor(elapsed / 1000) : null} />
      <ErrorDocuments />
    </div>
  );
}
