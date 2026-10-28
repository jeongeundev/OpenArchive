// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { ApiError, auditCsvUrl, countAudit, listAudit, listUsers } from "@/lib/api";
import { ACTION_LABEL, ACTIONS, actionBadgeClass, actionName, auditSince, describeDetail } from "@/lib/audit";
import type { AuditAction, AuditCounts, AuditEntry, UserSummary } from "@/lib/types";

const PAGE_SIZE = 50;

const TIME_FORMATTER = new Intl.DateTimeFormat("ko-KR", { hour: "numeric", minute: "2-digit", second: "2-digit", hour12: true });
const DAY_FORMATTER = new Intl.DateTimeFormat("ko-KR", { month: "long", day: "numeric" });

function Icon({ path }: { path: string }): React.ReactElement {
  return <svg aria-hidden="true" className="size-4 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}><path d={path} strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function text(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

/** 사용자 칸. 앱 행위자가 없으면 기록 경로로 누가 했는지를 적는다 (ADR-055). */
function actorLabel(entry: AuditEntry): string {
  if (entry.actor) return entry.actor;
  if (entry.actor_via === "share") return `공유: ${text(entry.detail.share_name)}`;
  if (entry.actor_via === "worker") return "시스템(워커)";
  if (entry.actor_via === "cli") return "운영자 CLI";
  return `직접 접속(${entry.db_role})`;
}

export default function AuditPage(): React.ReactElement {
  const { auth, loading: authLoading } = useAuth();
  const [items, setItems] = useState<AuditEntry[]>([]);
  const [nextBeforeId, setNextBeforeId] = useState<number | null>(null);
  const [users, setUsers] = useState<UserSummary[]>([]);
  const [actor, setActor] = useState("");
  const [action, setAction] = useState<AuditAction | "">(() => {
    const value = typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("action");
    return ACTIONS.includes(value as AuditAction) ? value as AuditAction : "";
  });
  const [loaded, setLoaded] = useState(false);
  const [resolvedRequest, setResolvedRequest] = useState<string | null>(null);
  const [period, setPeriod] = useState<"today" | "7d" | "30d" | "all">("7d");
  const [since, setSince] = useState<string | undefined>(() => auditSince("7d", new Date()));
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [cursors, setCursors] = useState<(number | undefined)[]>([undefined]);
  const [counts, setCounts] = useState<AuditCounts>({ total: 0, actions: {} });
  const [countError, setCountError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const beforeId = cursors[cursors.length - 1];
  const requestKey = JSON.stringify([actor, action, since, q, beforeId]);
  const busy = resolvedRequest !== requestKey;
  function resetPage(): void { setCursors([undefined]); }

  useEffect(() => {
    const timer = setTimeout(() => { setQ(search); setCursors([undefined]); }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (authLoading || !auth.is_admin) return;
    const controller = new AbortController();
    countAudit({ actor, since, q }, controller.signal)
      .then(result => { if (!controller.signal.aborted) { setCounts(result); setCountError(null); } })
      .catch(() => { if (!controller.signal.aborted) setCountError("동작별 건수를 불러오지 못했습니다."); });
    return () => controller.abort();
  }, [auth.is_admin, authLoading, actor, since, q]);

  useEffect(() => {
    if (authLoading || !auth.is_admin) return;
    const controller = new AbortController();
    listUsers(controller.signal)
      .then((userItems) => {
        if (!controller.signal.aborted) setUsers(userItems);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [auth.is_admin, authLoading]);

  useEffect(() => {
    if (authLoading || !auth.is_admin) return;
    const controller = new AbortController();
    listAudit({ actor, action, since, q, limit: PAGE_SIZE, beforeId }, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        setItems(page.items);
        setNextBeforeId(page.next_before_id);
        setLoaded(true);
        setResolvedRequest(requestKey);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setResolvedRequest(requestKey);
          setError(reason instanceof ApiError ? reason.detail : "감사 로그를 불러오지 못했습니다.");
        }
      });
    return () => controller.abort();
  }, [auth.is_admin, authLoading, actor, action, since, q, beforeId, requestKey]);

  if (authLoading) return <p className="text-sm text-muted">불러오는 중…</p>;
  if (!auth.is_admin) return <p className="text-sm text-muted">관리자 권한이 필요합니다.</p>;

  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="mb-2 text-sm font-semibold text-accent">관리</p>
          <h1 className="text-4xl font-semibold text-strong">감사 로그</h1>
          <p className="mt-3 text-sm text-muted">문서·열람 범위·폴더·외부 공유·그룹·사용자·비밀번호·API 토큰의 변경과 원본 내려받기·미리보기 기록입니다.</p>
        </div>
        <a className="inline-flex items-center gap-2 rounded-lg border border-line-strong px-4 py-4 text-sm font-medium text-body" download href={auditCsvUrl({ actor, action, since, q })}>
          <Icon path="M12 3v12 m-4-4 4 4 4-4 M4 15v5h16v-5" />CSV 내려받기
        </a>
      </div>
      <div className="flex items-center gap-3 rounded-xl bg-sunken px-4 py-4 text-sm text-body">
        <Icon path="M7 10V7a5 5 0 0 1 10 0v3 M5 10h14v11H5Z" />
        <p>기록은 고치거나 지울 수 없습니다. 대상 문서의 제목과 폴더 이름만 보이고, 문서 본문은 보이지 않습니다.</p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs font-medium text-muted">사용자
          <select className="mt-2 block rounded-lg border border-line-strong bg-card px-3 py-2 text-sm text-body" onChange={(event) => { setActor(event.target.value); resetPage(); }} value={actor}>
            <option value="">전체</option>
            {users.map((user) => <option key={user.id} value={user.username}>{user.username}</option>)}
          </select>
        </label>
        <label className="text-xs font-medium text-muted">기간
          <select className="mt-2 block rounded-lg border border-line-strong bg-card px-3 py-2 text-sm text-body" value={period} onChange={event => {
            const value = event.target.value as typeof period;
            setPeriod(value); setSince(auditSince(value, new Date())); resetPage();
          }}>
            <option value="today">오늘</option><option value="7d">최근 7일</option><option value="30d">최근 30일</option><option value="all">전체</option>
          </select>
        </label>
        <label className="min-w-48 flex-1 text-xs font-medium text-muted">대상 문서
          <input className="mt-2 block w-full rounded-lg border border-line-strong bg-card px-3 py-2 text-sm text-body" placeholder="문서 제목으로 찾기" value={search} onChange={event => setSearch(event.target.value)} />
        </label>
      </div>
      <div aria-label="동작 필터" className="flex flex-wrap gap-2">
        {(["", ...ACTIONS.filter(value => (counts.actions[value] ?? 0) > 0 || value === action)] as const).map(value => (
          <button key={value} type="button" aria-pressed={action === value} onClick={() => { setAction(value); resetPage(); }}
            className={`rounded-full border px-4 py-2 text-sm font-medium ${action === value ? "border-strong bg-strong text-on-strong" : "border-line-strong text-body"}`}>
            {value === "" ? `전체 ${counts.total}` : `${ACTION_LABEL[value]} ${counts.actions[value] ?? 0}`}
          </button>
        ))}
      </div>
      {countError ? <p role="alert" className="text-sm text-failed">{countError}</p> : null}

      {!loaded && error === null ? <p className="text-sm text-muted">불러오는 중…</p> : null}
      {loaded && items.length === 0 ? <p className="rounded-xl border border-dashed border-line-strong p-12 text-sm text-muted">기록이 없습니다.</p> : null}

      {loaded && items.length > 0 ? (
        <div className="overflow-x-auto rounded-xl border border-line bg-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line bg-sunken text-muted">
              <tr>
                <th className="px-4 py-4 font-medium">시각</th>
                <th className="px-4 py-4 font-medium">사용자</th>
                <th className="px-4 py-4 font-medium">동작</th>
                <th className="px-4 py-4 font-medium">대상 문서</th>
              </tr>
            </thead>
            <tbody>
              {items.map((entry) => {
                const description = describeDetail(entry);
                return (
                  <tr className="border-b border-line last:border-0" key={entry.id}>
                    <td className="whitespace-nowrap px-4 py-4 text-muted">
                      <time dateTime={entry.occurred_at}>{TIME_FORMATTER.format(new Date(entry.occurred_at))}<span className="mt-1 block text-xs">{DAY_FORMATTER.format(new Date(entry.occurred_at))}</span></time>
                    </td>
                    <td className="px-4 py-4 text-body"><div className="flex items-center gap-2"><span aria-hidden="true" className="flex size-7 shrink-0 items-center justify-center rounded-full bg-sunken text-xs font-semibold">{Array.from(actorLabel(entry))[0]}</span><span>{actorLabel(entry)}</span></div></td>
                    <td className="px-4 py-4 text-body">
                      <span className={`inline-block rounded px-2 py-1 text-xs font-medium ${actionBadgeClass(entry.action)}`}>{actionName(entry)}</span>
                      {description !== null ? <span className="mt-1 block text-xs text-muted">{description}</span> : null}
                    </td>
                    <td className="px-4 py-4 text-body">{entry.document_title ?? (typeof entry.detail.folder_name === "string" ? `폴더 「${entry.detail.folder_name}」` : "—")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {loaded ? <div className="flex items-center justify-between text-sm text-muted">
        <p>{items.length}건 표시</p>
        <div className="flex gap-2">
          <button type="button" className="rounded-lg border border-line-strong px-3 py-2 text-body disabled:text-faint" disabled={busy || cursors.length === 1} onClick={() => setCursors(current => current.slice(0, -1))}>이전</button>
          <button type="button" className="rounded-lg border border-line-strong px-3 py-2 text-body disabled:text-faint" disabled={busy || nextBeforeId === null} onClick={() => { if (nextBeforeId !== null) setCursors(current => [...current, nextBeforeId]); }}>다음</button>
        </div>
      </div> : null}
      {error !== null ? <p className="text-sm text-failed" role="alert">{error}</p> : null}
    </section>
  );
}
