import type { SystemStatus } from "@/lib/types";

export function StatusPanel({ status, error }: { status: SystemStatus | null; error: string | null }): React.ReactElement {
  return (
    <section className="space-y-4" aria-labelledby="system-status-heading">
      <h2 id="system-status-heading" className="text-sm font-medium text-neutral-400">시스템 상태</h2>
      {error !== null && <p className="border border-[#ef4444]/40 bg-[#ef4444]/10 px-4 py-3 text-sm text-[#ef4444]">상태 조회 실패: {error}</p>}
      {status === null ? (
        <div className="rounded-lg border border-neutral-800 bg-[#141414] p-6 text-sm text-neutral-500">상태를 조회하고 있습니다.</div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-neutral-800 bg-[#141414] p-6 md:col-span-2">
            <p className="text-sm font-medium text-neutral-400">정합성 검증</p>
            <p data-testid="consistency-count" className={`mt-2 text-5xl font-semibold ${status.inconsistent_documents === 0 ? "text-[#22c55e]" : "text-[#a3a3a3]"}`}>{status.inconsistent_documents}</p>
            <p className="mt-3 text-sm text-neutral-400">원본 버전과 청크 버전이 어긋난 문서 수. 재임베딩 중에만 증가했다가 0으로 돌아옵니다.</p>
          </div>
          <div className="rounded-lg border border-neutral-800 bg-[#141414] p-6">
            <p className="text-sm font-medium text-neutral-400">접속 DB 노드</p>
            <p className="mt-2 font-mono text-sm text-white">{status.node_address ?? "유닉스 소켓"}:{status.node_port}</p>
          </div>
          <div className="rounded-lg border border-neutral-800 bg-[#141414] p-6">
            <p className="text-sm font-medium text-neutral-400">임베딩 잡</p>
            <dl className="mt-2 flex gap-5 text-sm"><div><dt className="text-neutral-500">pending</dt><dd className="text-white">{status.jobs.pending}</dd></div><div><dt className="text-neutral-500">processing</dt><dd className="text-white">{status.jobs.processing}</dd></div><div><dt className="text-[#ef4444]">error</dt><dd className="text-[#ef4444]">{status.jobs.error}</dd></div></dl>
          </div>
          <div className="rounded-lg border border-neutral-800 bg-[#141414] p-6"><p className="text-sm font-medium text-neutral-400">임베딩 프로바이더</p><p className="mt-2 text-sm text-white">{status.embedding_provider}</p></div>
          <div className="rounded-lg border border-neutral-800 bg-[#141414] p-6"><p className="text-sm font-medium text-neutral-400">재연결 이벤트</p><p className="mt-2 text-sm text-neutral-500">아직 수집하지 않습니다.</p></div>
        </div>
      )}
    </section>
  );
}
