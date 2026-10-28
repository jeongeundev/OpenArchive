// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { ErrorDocuments } from "@/components/ErrorDocuments";
import { StatusPanel } from "@/components/StatusPanel";
import { useSystemStatus } from "@/lib/useSystemStatus";

export default function AdminStatusPage(): React.ReactElement {
  const { status, error, checkedAt, refresh } = useSystemStatus();
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
          <button type="button" onClick={refresh} className="rounded-lg border border-line-strong px-4 py-2 text-sm font-medium text-body hover:bg-sunken">새로고침</button>
        </div>
      </header>
      <StatusPanel status={status} error={error} />
      <ErrorDocuments />
    </div>
  );
}
