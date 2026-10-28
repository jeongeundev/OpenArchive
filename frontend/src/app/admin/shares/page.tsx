// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { ApiError, listAdminShares, revokeAdminShareToken, listRevokedShareTokens } from "@/lib/api";
import { formatExpiry, summarizeShares } from "@/lib/tokenExpiry";
import type { AdminShareSummary, AdminRevokedShareToken } from "@/lib/types";

const DATE_FORMATTER = new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" });
// 서버 `list_revoked_share_tokens`의 기본 상한. 이만큼 오면 누적 전체가 아니므로 「200+」로 적는다.
const REVOKED_LIMIT = 200;

export default function SharesPage(): React.ReactElement {
  const { auth, loading: authLoading } = useAuth();
  const [shares, setShares] = useState<AdminShareSummary[]>([]);
  const [revoked, setRevoked] = useState<AdminRevokedShareToken[]>([]);
  const [tab, setTab] = useState<"active" | "expired" | "revoked">("active");
  const [loaded, setLoaded] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (authLoading || !auth.is_admin) return;
    const controller = new AbortController();
    Promise.all([listAdminShares(controller.signal), listRevokedShareTokens(controller.signal)]).then(([items, history]) => {
      if (!controller.signal.aborted) { setShares(items); setRevoked(history); setLoaded(true); }
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof ApiError ? reason.detail : "외부 공유를 불러오지 못했습니다.");
    });
    return () => controller.abort();
  }, [authLoading, auth.is_admin]);

  async function revoke(share: AdminShareSummary, token: AdminShareSummary["tokens"][number]): Promise<void> {
    if (working || !window.confirm(`공유 「${share.name}」의 토큰 「${token.name}」을 폐기하시겠습니까? 이 토큰으로 더 이상 접속할 수 없습니다.`)) return;
    setWorking(true);
    setError(null);
    try {
      await revokeAdminShareToken(share.id, token.id);
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "토큰을 폐기하지 못했습니다.");
      setWorking(false);
      return;
    }
    setShares(current => current.map(item => item.id === share.id ? { ...item, tokens: item.tokens.filter(value => value.id !== token.id) } : item));
    try {
      setRevoked(await listRevokedShareTokens());
    } catch {
      setError("토큰은 폐기했지만 폐기 목록을 새로 불러오지 못했습니다.");
    } finally { setWorking(false); }
  }

  if (authLoading) return <p className="text-sm text-muted">불러오는 중…</p>;
  if (!auth.is_admin) return <p className="text-sm text-muted">관리자 권한이 필요합니다.</p>;

  const summary = summarizeShares(shares, new Date());
  const revokedLabel = revoked.length >= REVOKED_LIMIT ? `${REVOKED_LIMIT}+` : revoked.length;
  const activeShares = shares.filter(share => share.tokens.length === 0 || share.tokens.some(token => !token.expired));
  const expiredTokens = shares.flatMap(share => share.tokens.filter(token => token.expired).map(token => ({ share, token })));
  const empty = tab === "active" ? activeShares.length === 0 : tab === "expired" ? expiredTokens.length === 0 : revoked.length === 0;
  function tokenInfo(share: AdminShareSummary, token: AdminShareSummary["tokens"][number]): React.ReactElement {
    return <div className="space-y-1">
      <span className="text-body">{token.name}</span>
      {token.expired ? <span className="ml-2 rounded bg-failed-soft px-2 py-0.5 text-xs font-medium text-failed">만료</span> : null}
      <span className="block text-xs text-muted">{token.expires_at === null ? "만료 없음" : `만료 ${formatExpiry(token.expires_at)}`}</span>
      <span className="block text-xs text-muted">{token.last_used_at === null ? "사용 기록 없음" : `마지막 사용 ${DATE_FORMATTER.format(new Date(token.last_used_at))}`}</span>
      <button aria-label={`${token.name} 폐기`} className="text-muted hover:text-strong disabled:text-faint" disabled={working} onClick={() => void revoke(share, token)} type="button">폐기</button>
    </div>;
  }
  const headings = tab === "active" ? ["공유 이름", "소유자", "문서 수", "토큰"] : tab === "expired" ? ["공유 이름", "소유자", "문서 수", "토큰 · 만료 · 마지막 사용"] : ["공유 이름", "토큰 이름", "폐기 시각", "폐기한 사람"];
  return <section className="space-y-6">
    <div>
      <p className="mb-2 text-sm font-medium text-accent">관리</p>
      <h1 className="text-4xl font-semibold text-strong">외부 공유</h1>
      <p className="mt-3 text-sm text-muted">모든 사용자의 외부 공유 링크와 토큰 사용 기록을 확인하고, 필요하면 토큰을 폐기합니다.</p>
    </div>
    {error !== null ? <p className="text-sm text-failed" role="alert">{error}</p> : null}
    {!loaded ? error === null ? <p className="text-sm text-muted">불러오는 중…</p> : null : <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[["활성 링크", summary.active], ["7일 내 만료 예정", summary.expiring], ["최근 7일 토큰 사용", summary.recent], ["폐기된 토큰", revokedLabel]].map(([label, count]) => <div key={label} className="rounded-xl border border-line bg-card px-5 py-4"><p className="text-xs text-muted">{label}</p><p className="mt-2 text-3xl font-semibold text-strong">{count}</p></div>)}
      </div>
      <div role="tablist" aria-label="토큰 상태" className="flex gap-1 border-b border-line">
        {([["active", "활성", activeShares.length], ["expired", "만료", expiredTokens.length], ["revoked", "폐기", revokedLabel]] as const).map(([value, label, count]) => <button key={value} role="tab" aria-selected={tab === value} type="button" onClick={() => setTab(value)} className={`border-b-2 px-4 py-3 text-sm ${tab === value ? "border-strong font-semibold text-strong" : "border-transparent text-muted hover:text-strong"}`}>{label} {count}</button>)}
      </div>
      {empty ? <div className="flex min-h-72 flex-col items-center justify-center rounded-xl border border-dashed border-line-strong px-6 py-12 text-center">
        <div className="mb-4 flex size-12 items-center justify-center rounded-full bg-sunken text-muted"><svg aria-hidden="true" className="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}><path d="m10 13 4-4m-6 6-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2 2 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" strokeLinecap="round" strokeLinejoin="round" /></svg></div>
        <h2 className="font-semibold text-strong">{tab === "active" ? "외부 공유가 없습니다" : tab === "expired" ? "만료된 토큰이 없습니다" : "폐기된 토큰이 없습니다"}</h2>
        <p className="mt-3 max-w-lg text-sm leading-relaxed text-muted">사용자가 문서에서 외부 공유 링크를 만들면 여기에 모입니다. 관리자는 링크별 사용 기록을 보고 토큰을 폐기할 수 있습니다.</p>
        <a href="/admin/audit?action=share_changed" className="mt-4 text-sm font-medium text-accent">감사 로그에서 공유 기록 보기</a>
      </div> : <div className="overflow-x-auto rounded-xl border border-line bg-card">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-line bg-sunken text-muted"><tr>{headings.map(label => <th key={label} className="px-5 py-3 font-medium">{label}</th>)}</tr></thead>
          <tbody className="text-body">
            {tab === "active" ? activeShares.map(share => <tr key={share.id} className="border-b border-line align-top last:border-0">
              <td className="px-5 py-4 font-semibold text-strong">{share.name}</td><td className="px-5 py-4">{share.owner}</td>
              <td className="px-5 py-4"><span>{share.document_count}</span>{share.folder_count > 0 ? <span className="ml-1 text-muted">· 폴더 {share.folder_count}</span> : null}</td>
              <td className="space-y-3 px-5 py-4">{share.tokens.length === 0 ? "토큰 없음" : share.tokens.filter(token => !token.expired).map(token => <div key={token.id}>{tokenInfo(share, token)}</div>)}</td>
            </tr>) : tab === "expired" ? expiredTokens.map(({ share, token }) => <tr key={token.id} className="border-b border-line align-top last:border-0"><td className="px-5 py-4 font-semibold text-strong">{share.name}</td><td className="px-5 py-4">{share.owner}</td><td className="px-5 py-4">{share.document_count}</td><td className="px-5 py-4">{tokenInfo(share, token)}</td></tr>) : revoked.map(token => <tr key={token.id} className="border-b border-line last:border-0"><td className="px-5 py-4 font-semibold text-strong">{token.share_name}</td><td className="px-5 py-4">{token.token_name}</td><td className="px-5 py-4 text-muted">{DATE_FORMATTER.format(new Date(token.revoked_at))}</td><td className="px-5 py-4">{token.revoked_by ?? (token.actor_via === "cli" ? "운영자 CLI" : "직접 접속")}</td></tr>)}
          </tbody>
        </table>
      </div>}
    </>}
  </section>;
}
