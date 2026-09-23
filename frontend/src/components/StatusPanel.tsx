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
            <div className="mt-4 grid gap-5 md:grid-cols-2">
              <div>
                <p className="text-sm text-neutral-500">원본과 청크 버전</p>
                <p data-testid="consistency-count" className={`mt-2 text-5xl font-semibold ${status.inconsistent_documents === 0 ? "text-[#22c55e]" : "text-[#a3a3a3]"}`}>{status.inconsistent_documents}</p>
                <p className="mt-3 text-sm text-neutral-400">원본 버전과 청크 버전이 어긋난 문서 수. 재임베딩 중에만 증가했다가 0으로 돌아옵니다.</p>
              </div>
              <div>
                <p className="text-sm text-neutral-500">관계</p>
                <p data-testid="stale-edge-count" className={`mt-2 text-5xl font-semibold ${status.stale_edge_documents === 0 ? "text-[#22c55e]" : "text-[#a3a3a3]"}`}>{status.stale_edge_documents}</p>
                <p className="mt-3 text-sm text-neutral-400">{status.stale_edge_documents === 0 ? "관계까지 반영됨. 관계는 임베딩이 끝난 뒤 별도 잡으로 계산됩니다." : "관계가 아직 반영되지 않은 문서 수. 임베딩이 끝난 뒤 관계 계산이 따로 처리됩니다. 오래 내려오지 않으면 워커가 도는지 확인하고, 관계 잡이 재시도를 소진했다면 openarchive rebuild-edges로 복구합니다."}</p>
              </div>
            </div>
          </div>
          <div className="rounded-lg border border-neutral-800 bg-[#141414] p-6">
            <p className="text-sm font-medium text-neutral-400">접속 DB 노드</p>
            <p className="mt-2 font-mono text-sm text-white">{status.node_address ?? "유닉스 소켓"}:{status.node_port}</p>
          </div>
          <div className="rounded-lg border border-neutral-800 bg-[#141414] p-6">
            <p className="text-sm font-medium text-neutral-400">임베딩 잡</p>
            <dl className="mt-2 flex gap-5 text-sm"><div><dt className="text-neutral-500">pending</dt><dd className="text-white">{status.jobs.pending}</dd></div><div><dt className="text-neutral-500">processing</dt><dd className="text-white">{status.jobs.processing}</dd></div><div><dt className="text-neutral-500">회수 대기</dt><dd className="text-white">{status.jobs.recovery_pending}</dd></div><div><dt className="text-[#ef4444]">error</dt><dd className="text-[#ef4444]">{status.jobs.error}</dd></div></dl>
            <p className="mt-3 text-xs text-neutral-500">{status.zombie_timeout_minutes}분을 넘긴 잡은 워커의 다음 폴링에서 회수됩니다.</p>
            <p className="mt-1 text-xs text-neutral-500">최근 잡 완료 시각: {status.last_job_finished_at === null ? "기록 없음" : new Date(status.last_job_finished_at).toLocaleString("ko-KR")}</p>
          </div>
          <div className="rounded-lg border border-neutral-800 bg-[#141414] p-6"><p className="text-sm font-medium text-neutral-400">임베딩 프로바이더</p><p className="mt-2 text-sm text-white">{status.embedding_provider}</p></div>
        </div>
      )}
    </section>
  );
}
