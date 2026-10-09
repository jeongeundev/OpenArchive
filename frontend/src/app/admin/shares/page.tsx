"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { ApiError, listAdminShares, revokeAdminShareToken } from "@/lib/api";
import { formatExpiry } from "@/lib/tokenExpiry";
import type { AdminShareSummary } from "@/lib/types";

const DATE_FORMATTER = new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" });

export default function SharesPage(): React.ReactElement {
  const { auth, loading: authLoading } = useAuth();
  const [shares, setShares] = useState<AdminShareSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (authLoading || !auth.is_admin) return;
    const controller = new AbortController();
    listAdminShares(controller.signal).then(items => {
      if (!controller.signal.aborted) { setShares(items); setLoaded(true); }
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
      setShares(current => current.map(item => item.id === share.id ? { ...item, tokens: item.tokens.filter(value => value.id !== token.id) } : item));
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "토큰을 폐기하지 못했습니다.");
    } finally { setWorking(false); }
  }

  if (authLoading) return <p className="text-sm text-neutral-500">불러오는 중…</p>;
  if (!auth.is_admin) return <p className="text-sm text-neutral-500">관리자 권한이 필요합니다.</p>;

  return <section className="space-y-8">
    <div>
      <h1 className="text-4xl font-semibold text-white">외부 공유</h1>
      <p className="mt-3 text-sm text-neutral-400">모든 사용자의 공유와 토큰 사용 기록을 확인하고, 필요한 경우 토큰을 폐기합니다.</p>
    </div>
    {!loaded && error === null ? <p className="text-sm text-neutral-500">불러오는 중…</p> : null}
    {loaded && shares.length === 0 ? <p className="text-sm text-neutral-500">외부 공유가 없습니다.</p> : null}
    {loaded && shares.length > 0 ? <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-[#141414]">
      <table className="w-full text-left text-sm">
        <thead className="border-b border-neutral-800 text-neutral-400"><tr>{["공유 이름", "소유자", "생성일", "문서 수", "토큰"].map(label => <th className="px-4 py-3 font-medium" key={label}>{label}</th>)}</tr></thead>
        <tbody>{shares.map(share => <tr className="border-b border-neutral-800 last:border-0" key={share.id}>
          <td className="px-4 py-3 text-white">{share.name}</td>
          <td className="px-4 py-3 text-neutral-300">{share.owner}</td>
          <td className="px-4 py-3 text-neutral-400"><time dateTime={share.created_at}>{DATE_FORMATTER.format(new Date(share.created_at))}</time></td>
          <td className="px-4 py-3 text-neutral-300">{share.document_count}</td>
          <td className="px-4 py-3 text-neutral-400">{share.tokens.length === 0 ? "토큰 없음" : <ul className="space-y-3">{share.tokens.map(token => <li className="space-y-1" key={token.id}>
            <span className="text-neutral-300">{token.name}</span>
            {token.expired ? <span className="ml-2 rounded bg-[#ef4444]/10 px-2 py-0.5 text-xs font-medium text-[#ef4444]">만료</span> : null}
            <span className="block text-xs text-neutral-500">{token.expires_at === null ? "만료 없음" : `만료 ${formatExpiry(token.expires_at)}`}</span>
            <span className="block text-xs text-neutral-500">{token.last_used_at === null ? "사용 기록 없음" : `마지막 사용 ${DATE_FORMATTER.format(new Date(token.last_used_at))}`}</span>
            <button aria-label={`${token.name} 폐기`} className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600" disabled={working} onClick={() => void revoke(share, token)} type="button">폐기</button>
          </li>)}</ul>}</td>
        </tr>)}</tbody>
      </table>
    </div> : null}
    {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
  </section>;
}
