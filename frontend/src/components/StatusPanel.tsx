// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import type { SystemStatus } from "@/lib/types";

export function StatusPanel({ status, error }: { status: SystemStatus | null; error: string | null }): React.ReactElement {
  return (
    <section className="space-y-4" aria-labelledby="system-status-heading">
      <h2 id="system-status-heading" className="text-sm font-medium text-muted">시스템 상태</h2>
      {error !== null && <p className="border border-failed bg-failed-soft px-4 py-3 text-sm text-failed">상태 조회 실패: {error}</p>}
      {status === null ? (
        <div className="rounded-lg border border-line bg-card p-6 text-sm text-muted">상태를 조회하고 있습니다.</div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-line bg-card p-6 md:col-span-2">
            <p className="text-sm font-medium text-muted">정합성 검증</p>
            <div className="mt-4 grid gap-5 md:grid-cols-2">
              <div>
                <p className="text-sm text-muted">원본과 청크 버전</p>
                <p data-testid="consistency-count" className={`mt-2 text-5xl font-semibold ${status.inconsistent_documents === 0 ? "text-done" : "text-wait"}`}>{status.inconsistent_documents}</p>
                <p className="mt-3 text-sm text-muted">텍스트 버전과 청크 버전이 어긋난 문서 수. 재임베딩 중에만 증가했다가 0으로 돌아옵니다.</p>
              </div>
              <div>
                <p className="text-sm text-muted">관계</p>
                <p data-testid="stale-edge-count" className={`mt-2 text-5xl font-semibold ${status.stale_edge_documents === 0 ? "text-done" : "text-wait"}`}>{status.stale_edge_documents}</p>
                <p className="mt-3 text-sm text-muted">{status.stale_edge_documents === 0 ? "관계까지 반영됨. 관계는 임베딩이 끝난 뒤 별도 잡으로 계산됩니다." : "관계가 아직 반영되지 않은 문서 수. 임베딩이 끝난 뒤 관계 계산이 따로 처리됩니다. 오래 내려오지 않으면 워커가 도는지 확인하고, 관계 잡이 재시도를 소진했다면 openarchive rebuild-edges로 복구합니다."}</p>
              </div>
            </div>
          </div>
          <div className="rounded-lg border border-line bg-card p-6">
            <p className="text-sm font-medium text-muted">접속 DB 노드</p>
            <p className="mt-2 font-mono text-sm text-strong">{status.node_address ?? "유닉스 소켓"}:{status.node_port}</p>
          </div>
          <div className="rounded-lg border border-line bg-card p-6">
            <p className="text-sm font-medium text-muted">임베딩 잡</p>
            <dl className="mt-2 flex gap-5 text-sm"><div><dt className="text-muted">pending</dt><dd className="text-strong">{status.jobs.pending}</dd></div><div><dt className="text-muted">processing</dt><dd className="text-strong">{status.jobs.processing}</dd></div><div><dt className="text-muted">회수 대기</dt><dd className="text-strong">{status.jobs.recovery_pending}</dd></div><div><dt className="text-failed">error</dt><dd className="text-failed">{status.jobs.error}</dd></div></dl>
            <p className="mt-3 text-xs text-muted">워커가 {status.job_lease_seconds}초 동안 처리 중임을 알리지 않은 잡은 회수됩니다.</p>
            <p className="mt-1 text-xs text-muted">최근 잡 완료 시각: {status.last_job_finished_at === null ? "기록 없음" : new Date(status.last_job_finished_at).toLocaleString("ko-KR")}</p>
          </div>
          <div className="rounded-lg border border-line bg-card p-6">
            <p className="text-sm font-medium text-muted">텍스트 인식</p>
            <dl className="mt-2 flex gap-5 text-sm"><div><dt className="text-muted">인식 대기</dt><dd data-testid="extraction-pending" className="text-strong">{status.extraction_pending}</dd></div><div><dt className="text-failed">인식 실패</dt><dd data-testid="extraction-failed" className="text-failed">{status.extraction_failed}</dd></div></dl>
            <p className="mt-3 text-xs text-muted">이미지·스캔 PDF에서 워커가 텍스트를 인식하는 문서 수. 인식 실패는 원본 교체나 다시 추출로 다시 시도합니다.</p>
          </div>
          <div className="rounded-lg border border-line bg-card p-6">
            <p className="text-sm font-medium text-muted">미리보기 변환</p>
            <dl className="mt-2 flex gap-5 text-sm"><div><dt className="text-muted">대기</dt><dd data-testid="preview-pending" className="text-strong">{status.preview_pending}</dd></div><div><dt className="text-failed">실패</dt><dd data-testid="preview-failed" className="text-failed">{status.preview_failed}</dd></div><div><dt className="text-muted">변환기 없음</dt><dd data-testid="preview-unavailable" className="text-strong">{status.preview_unavailable}</dd></div></dl>
            <p className="mt-3 text-xs text-muted">한글·오피스 원본 판을 워커가 PDF로 바꾸는 판 수.</p>
            {status.preview_unavailable > 0 && <p className="mt-1 text-xs text-muted">변환기(rhwp·LibreOffice)·한글 글꼴·bubblewrap 설치를 확인하세요. 설치 뒤 openarchive rebuild-previews로 다시 겁니다.</p>}
          </div>
          <div className="rounded-lg border border-line bg-card p-6"><p className="text-sm font-medium text-muted">임베딩 프로바이더</p><p className="mt-2 text-sm text-strong">{status.embedding_provider}</p></div>
        </div>
      )}
    </section>
  );
}
