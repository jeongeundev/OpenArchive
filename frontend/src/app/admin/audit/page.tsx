// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { ApiError, auditCsvUrl, listAudit, listUsers } from "@/lib/api";
import { ACTION_LABEL, ACTIONS, actionName, describeDetail } from "@/lib/audit";
import type { AuditAction, AuditEntry, UserSummary } from "@/lib/types";
import { useUnmountSignal } from "@/lib/useUnmountSignal";

const PAGE_SIZE = 50;

const DATE_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  dateStyle: "medium",
  timeStyle: "medium",
});

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
  const [action, setAction] = useState<AuditAction | "">("");
  const [loaded, setLoaded] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const unmountSignal = useUnmountSignal();

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
    listAudit({ actor, action, limit: PAGE_SIZE }, controller.signal)
      .then((page) => {
        if (controller.signal.aborted) return;
        setItems(page.items);
        setNextBeforeId(page.next_before_id);
        setLoaded(true);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof ApiError ? reason.detail : "감사 로그를 불러오지 못했습니다.");
        }
      });
    return () => controller.abort();
  }, [auth.is_admin, authLoading, actor, action]);

  async function loadMore(): Promise<void> {
    if (nextBeforeId === null || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await listAudit(
        { actor, action, limit: PAGE_SIZE, beforeId: nextBeforeId },
        unmountSignal(),
      );
      setItems((current) => [...current, ...page.items]);
      setNextBeforeId(page.next_before_id);
    } catch (reason: unknown) {
      setError(reason instanceof ApiError ? reason.detail : "감사 로그를 불러오지 못했습니다.");
    } finally {
      setLoadingMore(false);
    }
  }

  if (authLoading) return <p className="text-sm text-muted">불러오는 중…</p>;
  if (!auth.is_admin) return <p className="text-sm text-muted">관리자 권한이 필요합니다.</p>;

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-strong">감사 로그</h1>
        <p className="mt-3 text-sm text-muted">
          문서와 열람 범위·폴더·외부 공유·그룹·사용자·비밀번호·API 토큰의 변경, 원본 내려받기·미리보기 기록입니다. 기록은 고치거나 지울 수 없습니다.
          대상 문서의 제목과 폴더 이름만 보이고 문서 텍스트는 보이지 않습니다.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-4 rounded-lg border border-line bg-card p-6">
        <label className="text-sm text-muted">사용자
          <select className="mt-2 block rounded-lg border border-line bg-card px-4 py-3 text-body" onChange={(event) => setActor(event.target.value)} value={actor}>
            <option value="">전체</option>
            {users.map((user) => <option key={user.id} value={user.username}>{user.username}</option>)}
          </select>
        </label>
        <label className="text-sm text-muted">동작
          <select className="mt-2 block rounded-lg border border-line bg-card px-4 py-3 text-body" onChange={(event) => setAction(event.target.value as AuditAction | "")} value={action}>
            <option value="">전체</option>
            {ACTIONS.map((value) => <option key={value} value={value}>{ACTION_LABEL[value]}</option>)}
          </select>
        </label>
        <a className="text-sm text-muted hover:text-accent" download href={auditCsvUrl({ actor, action })}>CSV 내려받기</a>
      </div>

      {!loaded && error === null ? <p className="text-sm text-muted">불러오는 중…</p> : null}
      {loaded && items.length === 0 ? <p className="text-sm text-muted">기록이 없습니다.</p> : null}

      {loaded && items.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-line bg-card">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-line text-muted">
              <tr>
                <th className="px-4 py-3 font-medium">시각</th>
                <th className="px-4 py-3 font-medium">사용자</th>
                <th className="px-4 py-3 font-medium">동작</th>
                <th className="px-4 py-3 font-medium">대상 문서</th>
              </tr>
            </thead>
            <tbody>
              {items.map((entry) => {
                const description = describeDetail(entry);
                return (
                  <tr className="border-b border-line last:border-0" key={entry.id}>
                    <td className="whitespace-nowrap px-4 py-3 text-muted">
                      <time dateTime={entry.occurred_at}>{DATE_FORMATTER.format(new Date(entry.occurred_at))}</time>
                    </td>
                    <td className="px-4 py-3 text-body">{actorLabel(entry)}</td>
                    <td className="px-4 py-3 text-body">
                      <span>{actionName(entry)}</span>
                      {description !== null ? <span className="block text-muted">{description}</span> : null}
                    </td>
                    <td className="px-4 py-3 text-body">{entry.document_title ?? (typeof entry.detail.folder_name === "string" ? `폴더 「${entry.detail.folder_name}」` : "—")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {nextBeforeId !== null ? (
        <button className="text-sm text-muted hover:text-strong disabled:text-faint" disabled={loadingMore} onClick={() => void loadMore()} type="button">더 보기</button>
      ) : null}
      {error !== null ? <p className="text-sm text-failed" role="alert">{error}</p> : null}
    </section>
  );
}
