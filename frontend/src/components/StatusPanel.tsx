// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { outageSeconds, outageNodes, outageAuditUrl, attentionCount, attentionTarget, bannerKind, failureTotal, stageBadge, type HealthKind } from "@/lib/systemHealth";
import type { DbOutage, SystemStatus } from "@/lib/types";

const BADGE_CLASS: Record<HealthKind, string> = {
  ok: "bg-done-soft text-done",
  attention: "bg-attention-soft text-attention",
  failed: "bg-failed-soft text-failed",
};
const STAGE_LABEL: Record<HealthKind, string> = { ok: "정상", attention: "확인 필요", failed: "실패" };

function Icon({ path, className = "size-4" }: { path: string; className?: string }): React.ReactElement {
  return <svg aria-hidden="true" className={`${className} shrink-0`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}><path d={path} /></svg>;
}

function Badge({ kind, label }: { kind: HealthKind; label: string }): React.ReactElement {
  return <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${BADGE_CLASS[kind]}`}>{label}</span>;
}

function Stat({ label, value, testId, className = "text-strong" }: { label: string; value: number; testId?: string; className?: string }): React.ReactElement {
  return <div><p data-testid={testId} className={`text-3xl font-semibold ${className}`}>{value}</p><p className="mt-1 text-xs text-muted">{label}</p></div>;
}

function StageCard({ id, step, name, kind, description, highlighted = false, children }: {
  id: string; step: number; name: string; kind: HealthKind; description: string; highlighted?: boolean; children: React.ReactNode;
}): React.ReactElement {
  return (
    <div id={id} data-testid={id} className={`rounded-xl border p-5 ${highlighted ? "border-attention bg-attention-soft" : "border-line bg-card"}`}>
      <header className="flex items-center gap-2">
        <span className="flex size-6 items-center justify-center rounded-full bg-strong text-xs font-semibold text-on-strong">{step}</span>
        <p className="text-sm font-semibold text-strong">{name}</p>
        <span className="ml-auto"><Badge kind={kind} label={STAGE_LABEL[kind]} /></span>
      </header>
      <div className="mt-5 flex gap-6">{children}</div>
      <p className="mt-4 text-xs text-muted">{description}</p>
    </div>
  );
}

export function StatusPanel({ status, error, outages = [], outagesError = null, unresponsiveSeconds = null, lastSuccessAt = null }: {
  status: SystemStatus | null; error: string | null; outages?: DbOutage[]; outagesError?: string | null;
  unresponsiveSeconds?: number | null; lastSuccessAt?: Date | null;
}): React.ReactElement {
  return (
    <section className="space-y-8" aria-labelledby="system-status-heading">
      <h2 id="system-status-heading" className="sr-only">시스템 상태</h2>
      {error !== null && <p className="border border-failed bg-failed-soft px-4 py-3 text-sm text-failed">상태 조회 실패: {error}</p>}
      {status === null ? (
        <>
          {unresponsiveSeconds !== null && <UnresponsiveBanner seconds={unresponsiveSeconds} lastSuccessAt={lastSuccessAt} />}
          <div className="rounded-lg border border-line bg-card p-6 text-sm text-muted">상태를 조회하고 있습니다.</div>
        </>
      ) : (
        <StatusBody status={status} outages={outages} outagesError={outagesError} unresponsiveSeconds={unresponsiveSeconds} lastSuccessAt={lastSuccessAt} />
      )}
    </section>
  );
}

function StatusBody({ status, outages, outagesError, unresponsiveSeconds, lastSuccessAt }: {
  status: SystemStatus; outages: DbOutage[]; outagesError: string | null; unresponsiveSeconds: number | null; lastSuccessAt: Date | null;
}): React.ReactElement {
  const banner = bannerKind(status);
  const attention = attentionCount(status);
  const target = attentionTarget(status);
  const consistent = status.inconsistent_documents === 0 && status.stale_edge_documents === 0;
  const preview = stageBadge(status, "preview");
  return (
    <>
      {unresponsiveSeconds !== null ? (
        <UnresponsiveBanner seconds={unresponsiveSeconds} lastSuccessAt={lastSuccessAt} />
      ) : <div data-testid="health-banner" className={`flex flex-wrap items-center gap-4 rounded-2xl px-6 py-5 ${banner === "ok" ? "bg-done-soft text-done" : "bg-failed-soft text-failed"}`}>
        <span className={`flex size-10 items-center justify-center rounded-full text-on-strong ${banner === "ok" ? "bg-done" : "bg-failed"}`}>
          <Icon className="size-5" path={banner === "ok" ? "M5 12l5 5 9-10" : "M12 7v6 M12 17h.01"} />
        </span>
        <div className="flex-1">
          <p className="text-lg font-semibold">{banner === "ok" ? "모든 처리가 정상입니다" : "확인이 필요한 처리가 있습니다"}</p>
          <p className="mt-1 text-sm text-body">
            처리 실패 {failureTotal(status)}건 · 정합성 불일치 {status.inconsistent_documents}건 · 확인이 필요한 항목 {attention}건{attention > 0 ? " (미리보기 변환기)" : ""}
          </p>
        </div>
        {target !== null && <a href={`#${target}`} className="text-sm font-medium text-strong underline underline-offset-4">확인할 항목 보기</a>}
      </div>}

      <div className="space-y-3">
        <h3 className="text-sm font-semibold text-muted">연결</h3>
        <div className="grid gap-4 md:grid-cols-3">
          <div className="rounded-xl border border-line bg-card p-5">
            <div className="flex items-center justify-between"><p className="text-sm font-medium text-muted">접속 DB 노드</p><Badge kind="ok" label="연결됨" /></div>
            <p className="mt-4 font-mono text-2xl font-semibold text-strong">{status.node_address ?? "유닉스 소켓"}:{status.node_port}</p>
            <p className="mt-2 text-xs text-muted">장애 전환 시 새 Primary로 바뀝니다</p>
          </div>
          <div className="rounded-xl border border-line bg-card p-5">
            <div className="flex items-center justify-between"><p className="text-sm font-medium text-muted">임베딩 프로바이더</p><Badge kind="ok" label="사용 중" /></div>
            <p className="mt-4 font-mono text-2xl font-semibold text-strong">{status.embedding_provider}</p>
            <p className="mt-2 text-xs text-muted">최근 잡 완료 {status.last_job_finished_at === null ? "기록 없음" : new Date(status.last_job_finished_at).toLocaleString("ko-KR")}</p>
          </div>
          <div id="consistency-card" data-testid="consistency-card" className="rounded-xl border border-line bg-card p-5">
            <div className="flex items-center justify-between"><p className="text-sm font-medium text-muted">정합성 검증</p><Badge kind={consistent ? "ok" : "failed"} label={consistent ? "일치" : "불일치"} /></div>
            <div className="mt-4 grid grid-cols-2 gap-4">
              <div>
                <p data-testid="consistency-count" className={`text-3xl font-semibold ${status.inconsistent_documents === 0 ? "text-done" : "text-wait"}`}>{status.inconsistent_documents}</p>
                <p className="mt-1 text-xs text-muted">원본과 청크 버전 불일치</p>
                <p className="mt-2 text-xs text-faint">텍스트 버전과 청크 버전이 어긋난 문서 수. 재임베딩 중에만 증가했다가 0으로 돌아옵니다.</p>
              </div>
              <div>
                <p data-testid="stale-edge-count" className={`text-3xl font-semibold ${status.stale_edge_documents === 0 ? "text-done" : "text-wait"}`}>{status.stale_edge_documents}</p>
                <p className="mt-1 text-xs text-muted">관계 미반영</p>
                <p className="mt-2 text-xs text-faint">{status.stale_edge_documents === 0 ? "관계까지 반영됨." : "오래 내려오지 않으면 워커가 도는지 확인하고, 관계 잡이 재시도를 소진했다면 openarchive rebuild-edges로 복구합니다."}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold text-muted">처리 파이프라인</h3>
          <p className="text-xs text-muted">문서를 올리면 왼쪽부터 차례로 처리됩니다</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <StageCard id="stage-extraction" step={1} name="텍스트 인식" kind={stageBadge(status, "extraction")} description="이미지·스캔 PDF의 글자를 워커가 인식합니다. 실패 시 원본 교체나 다시 추출로 재시도합니다.">
            <Stat label="인식 대기" value={status.extraction_pending} testId="extraction-pending" />
            <Stat label="인식 실패" value={status.extraction_failed} testId="extraction-failed" className={status.extraction_failed > 0 ? "text-failed" : "text-strong"} />
          </StageCard>
          <StageCard id="stage-embedding" step={2} name="임베딩" kind={stageBadge(status, "embedding")} description={`워커가 ${status.job_lease_seconds}초 동안 응답하지 않으면 잡을 회수해 다시 처리합니다.`}>
            <Stat label="대기" value={status.jobs.pending} />
            <Stat label="처리 중" value={status.jobs.processing} />
            <Stat label="회수 대기" value={status.jobs.recovery_pending} />
            <Stat label="오류" value={status.jobs.error} className={status.jobs.error > 0 ? "text-failed" : "text-strong"} />
          </StageCard>
          <StageCard id="stage-relations" step={3} name="관계 계산" kind={stageBadge(status, "relations")} description="임베딩이 끝난 뒤 별도 잡으로 문서 간 관계를 계산합니다.">
            <Stat label="미반영" value={status.stale_edge_documents} className={status.stale_edge_documents > 0 ? "text-wait" : "text-strong"} />
          </StageCard>
          <StageCard id="stage-preview" step={4} name="미리보기 변환" kind={preview} highlighted={status.preview_unavailable > 0} description="한글·오피스 원본을 PDF로 바꿔 미리보기를 만듭니다.">
            <Stat label="대기" value={status.preview_pending} testId="preview-pending" />
            <Stat label="실패" value={status.preview_failed} testId="preview-failed" className={status.preview_failed > 0 ? "text-failed" : "text-strong"} />
            <Stat label="변환기 없음" value={status.preview_unavailable} testId="preview-unavailable" className={status.preview_unavailable > 0 ? "text-attention" : "text-strong"} />
          </StageCard>
        </div>
      </div>

      <OutageRecords items={outages} error={outagesError} />

      {status.preview_unavailable > 0 && (
        <div id="preview-unavailable-notice" data-testid="preview-unavailable-notice" className="flex items-start gap-3 rounded-xl bg-attention-soft px-5 py-4 text-sm text-body">
          <span className="text-attention"><Icon path="M12 3 2 20h20Z M12 10v4 M12 17h.01" /></span>
          <p>미리보기 변환기가 없는 문서가 {status.preview_unavailable}건 있습니다. 변환기(rhwp·LibreOffice)·한글 글꼴·bubblewrap 설치를 확인한 뒤 서버에서 <code className="rounded bg-card px-1.5 py-0.5 font-mono text-xs text-strong">openarchive rebuild-previews</code>를 실행하세요.</p>
        </div>
      )}
    </>
  );
}


function OutageBadge({ kind }: { kind: DbOutage["kind"] }): React.ReactElement {
  return <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${kind === "promotion" ? "bg-attention-soft text-attention" : "bg-wait-soft text-wait"}`}>{kind === "promotion" ? "Primary 승격" : "연결 일시 중단"}</span>;
}

function outageTime(value: string): string {
  return new Date(value).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true });
}

function OutageRecords({ items, error }: { items: DbOutage[]; error: string | null }): React.ReactElement {
  const recent = items[0];
  return <section className="space-y-4" aria-label="장애 기록">
    <h3 className="text-sm font-semibold text-muted">장애 기록</h3>
    <p className="text-xs text-muted">API 서버가 겪은 DB 연결 중단과 Primary 승격이 여기에 남습니다. 앱 연결에 영향이 없는 장애(Replica·etcd 한 대)는 클러스터 도구로 확인합니다. API 서버가 내려가 있던 동안의 장애는 기록되지 않습니다.</p>
    {error ? <p role="alert" className="text-sm text-failed">{error}</p> : !recent ?
      <div className="rounded-xl border border-dashed border-line-strong p-6 text-sm text-muted">기록된 DB 장애가 없습니다</div> :
      <>
        <div className="space-y-3 rounded-xl border border-line bg-card p-6 text-sm text-body">
          <OutageBadge kind={recent.kind} />
          <p className="font-semibold text-strong">{outageTime(recent.first_failure_at)} → {outageTime(recent.recovered_at)} ({outageSeconds(recent)}초)</p>
          <p className="text-xs text-muted">마지막 정상 {outageTime(recent.last_ok_at)}</p>
          <p>Primary {outageNodes(recent)}</p>
          <p>타임라인 {recent.timeline_before ?? "기록 없음"} → {recent.timeline_after ?? "기록 없음"}</p>
          <p>그 사이 요청 자동 재시도 성공 {recent.requests_retried}건 · 503 응답 {recent.requests_unavailable}건 · <span className={recent.server_errors === 0 ? "text-done" : "text-failed"}>서버 오류 {recent.server_errors}건</span></p>
          <a className="text-accent underline" href={outageAuditUrl(recent.first_failure_at)}>이 시간대 감사 로그 보기</a>
        </div>
        {items.length > 1 && <div className="overflow-x-auto rounded-xl border border-line"><table className="w-full text-left text-sm text-body">
          <thead className="bg-sunken text-muted"><tr>{["시각", "종류", "중단 시간", "노드 전후", "타임라인 전후", "서버 오류"].map(label => <th key={label} className="p-3 font-medium">{label}</th>)}</tr></thead>
          <tbody>{items.slice(1).map(item => <tr key={item.id} className="border-t border-line">
            <td className="p-3">{new Date(item.first_failure_at).toLocaleString("ko-KR")}</td>
            <td className="p-3"><OutageBadge kind={item.kind} /></td><td className="p-3">{outageSeconds(item)}초</td>
            <td className="p-3">{outageNodes(item)}</td><td className="p-3">{item.timeline_before ?? "기록 없음"} → {item.timeline_after ?? "기록 없음"}</td>
            <td className={`p-3 ${item.server_errors === 0 ? "text-done" : "text-failed"}`}>서버 오류 {item.server_errors}건</td>
          </tr>)}</tbody>
        </table></div>}
      </>}
  </section>;
}


function UnresponsiveBanner({ seconds, lastSuccessAt }: { seconds: number; lastSuccessAt: Date | null }): React.ReactElement {
  return <div data-testid="health-banner" className="rounded-2xl bg-failed-soft px-6 py-5 text-failed">
    <p className="text-lg font-semibold">DB 응답 없음 — {seconds}초째</p>
    <p className="mt-1 text-sm">마지막 정상 {lastSuccessAt?.toLocaleTimeString("ko-KR") ?? "기록 없음"} · 요청은 자동으로 다시 시도합니다</p>
  </div>;
}
