"use client";

import { ErrorDocuments } from "@/components/ErrorDocuments";
import { useAuth } from "@/components/AuthProvider";
import { StatusPanel } from "@/components/StatusPanel";
import { useSystemStatus } from "@/lib/useSystemStatus";

export default function AdminStatusPage(): React.ReactElement {
  const { auth, loading: authLoading } = useAuth();

  if (authLoading) return <p className="text-sm text-neutral-500">불러오는 중…</p>;
  if (!auth.authenticated) return <p className="text-sm text-neutral-500">로그인이 필요합니다.</p>;

  return <SystemStatusContent />;
}

function SystemStatusContent(): React.ReactElement {
  const { status, error } = useSystemStatus();
  return <div className="space-y-8"><header><p className="text-sm text-neutral-500">운영·데모 전용</p><h1 className="mt-2 text-4xl font-semibold text-white">시스템 상태</h1><p className="mt-3 text-sm text-neutral-400">연결이 잠시 끊겨도 마지막 관측값을 유지하며 짧은 중단 후 자동 복구 과정을 확인합니다.</p></header><StatusPanel status={status} error={error} /><ErrorDocuments /></div>;
}
