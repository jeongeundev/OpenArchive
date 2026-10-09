"use client";

import { useEffect, useState } from "react";

import { useAuth } from "@/components/AuthProvider";
import { ApiError, listAudit, listUsers } from "@/lib/api";
import {
  VISIBILITY_LABEL,
  type AuditAction,
  type AuditEntry,
  type UserSummary,
  type Visibility,
} from "@/lib/types";
import { useUnmountSignal } from "@/lib/useUnmountSignal";

const PAGE_SIZE = 50;

const ACTION_LABEL: Record<AuditAction, string> = {
  owner_changed: "소유자 변경",
  document_created: "문서 생성",
  text_updated: "텍스트 수정",
  document_deleted: "영구 삭제",
  document_trashed: "휴지통 이동",
  document_restored: "복원",
  access_changed: "열람 범위 변경",
  folder_access_changed: "폴더 열람 범위 변경",
  group_member_changed: "그룹 구성원 변경",
  original_replaced: "원본 교체",
  original_downloaded: "원본 내려받기",
  original_previewed: "원본 미리보기",
};

const ACTIONS = Object.keys(ACTION_LABEL) as AuditAction[];

const DATE_FORMATTER = new Intl.DateTimeFormat("ko-KR", {
  dateStyle: "medium",
  timeStyle: "medium",
});

function text(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function visibilityLabel(value: unknown): string {
  return value === "public" || value === "private" ? VISIBILITY_LABEL[value as Visibility] : text(value);
}

/** 동작 칸의 이름. 버전·판 번호는 이름에 붙인다. */
function actionLabel(entry: AuditEntry): string {
  const label = ACTION_LABEL[entry.action] ?? entry.action;
  if (entry.action === "text_updated") return `${label}(v${text(entry.detail.version)})`;
  if (entry.action === "original_replaced" || entry.action === "original_downloaded" || entry.action === "original_previewed") {
    return `${label}(판 ${text(entry.detail.file_version)})`;
  }
  return label;
}

/** 동작에 덧붙는 설명 — 무엇이 어떻게 바뀌었나. 없으면 null. */
function actionDescription(entry: AuditEntry): string | null {
  const { detail } = entry;
  if (entry.action === "owner_changed") {
    return `${detail.kind === "folder" ? `폴더 「${text(detail.folder_name)}」 ` : ""}${text(detail.before)} → ${text(detail.after)}`;
  }
  if (entry.action === "access_changed" || entry.action === "folder_access_changed") {
    if (detail.kind === "inherit") {
      const before = detail.before === "folder" ? "폴더 범위 따름" : "개별 지정";
      const after = detail.after === "folder" ? "폴더 범위 따름" : "개별 지정";
      return `${before} → ${after}`;
    }
    if (detail.kind === "folder") {
      const before = detail.before === null ? "폴더 없음" : `폴더 「${text(detail.before)}」`;
      const after = detail.after === null ? "폴더 없음" : `${detail.before === null ? "폴더 " : ""}「${text(detail.after)}」`;
      return `${before} → ${after}`;
    }
    if (detail.kind === "visibility") {
      return `${visibilityLabel(detail.before)} → ${visibilityLabel(detail.after)}`;
    }
    if (detail.kind === "grant") {
      const type = detail.grantee_type === "group" ? "그룹" : "사용자";
      const change = detail.change === "removed" ? "제거" : "추가";
      return `${type} ${text(detail.grantee)} ${change}`;
    }
  }
  if (entry.action === "group_member_changed") {
    return detail.change === "removed"
      ? `${text(detail.group)}에서 ${text(detail.user)} 제거`
      : `${text(detail.group)}에 ${text(detail.user)} 추가`;
  }
  return null;
}

/** 사용자 칸. 앱 행위자가 없으면 기록 경로로 누가 했는지를 적는다 (ADR-055). */
function actorLabel(entry: AuditEntry): string {
  if (entry.actor) return entry.actor;
  if (entry.actor_via === "share") return `공유: ${text(entry.detail.share_name)}`;
  if (entry.actor_via === "worker") return "시스템(텍스트 인식)";
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

  if (authLoading) return <p className="text-sm text-neutral-500">불러오는 중…</p>;
  if (!auth.is_admin) return <p className="text-sm text-neutral-500">관리자 권한이 필요합니다.</p>;

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-white">감사 로그</h1>
        <p className="mt-3 text-sm text-neutral-400">
          문서와 열람 범위·그룹 구성원의 변경, 원본 내려받기·미리보기 기록입니다. 기록은 고치거나 지울 수 없습니다.
          대상 문서는 제목만 보입니다.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-4 rounded-lg border border-neutral-800 bg-[#141414] p-6">
        <label className="text-sm text-neutral-400">사용자
          <select className="mt-2 block rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300" onChange={(event) => setActor(event.target.value)} value={actor}>
            <option value="">전체</option>
            {users.map((user) => <option key={user.id} value={user.username}>{user.username}</option>)}
          </select>
        </label>
        <label className="text-sm text-neutral-400">동작
          <select className="mt-2 block rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300" onChange={(event) => setAction(event.target.value as AuditAction | "")} value={action}>
            <option value="">전체</option>
            {ACTIONS.map((value) => <option key={value} value={value}>{ACTION_LABEL[value]}</option>)}
          </select>
        </label>
      </div>

      {!loaded && error === null ? <p className="text-sm text-neutral-500">불러오는 중…</p> : null}
      {loaded && items.length === 0 ? <p className="text-sm text-neutral-500">기록이 없습니다.</p> : null}

      {loaded && items.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-[#141414]">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-neutral-800 text-neutral-400">
              <tr>
                <th className="px-4 py-3 font-medium">시각</th>
                <th className="px-4 py-3 font-medium">사용자</th>
                <th className="px-4 py-3 font-medium">동작</th>
                <th className="px-4 py-3 font-medium">대상 문서</th>
              </tr>
            </thead>
            <tbody>
              {items.map((entry) => {
                const description = actionDescription(entry);
                return (
                  <tr className="border-b border-neutral-800 last:border-0" key={entry.id}>
                    <td className="whitespace-nowrap px-4 py-3 text-neutral-400">
                      <time dateTime={entry.occurred_at}>{DATE_FORMATTER.format(new Date(entry.occurred_at))}</time>
                    </td>
                    <td className="px-4 py-3 text-neutral-300">{actorLabel(entry)}</td>
                    <td className="px-4 py-3 text-neutral-300">
                      <span>{actionLabel(entry)}</span>
                      {description !== null ? <span className="block text-neutral-500">{description}</span> : null}
                    </td>
                    <td className="px-4 py-3 text-neutral-300">{entry.document_title ?? (typeof entry.detail.folder_name === "string" ? `폴더 「${entry.detail.folder_name}」` : "—")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {nextBeforeId !== null ? (
        <button className="text-sm text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600" disabled={loadingMore} onClick={() => void loadMore()} type="button">더 보기</button>
      ) : null}
      {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
    </section>
  );
}
