// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { VISIBILITY_LABEL, type AuditAction, type Visibility } from "@/lib/types";

/** 감사 기록의 동작 이름·설명에 쓰는 최소 모양 — 관리자 감사 로그와 문서 활동 기록이 함께 쓴다. */
export interface AuditRecord {
  action: AuditAction;
  actor: string | null;
  detail: Record<string, unknown>;
}

export const ACTION_LABEL: Record<AuditAction, string> = {
  share_changed: "외부 공유 변경",
  group_changed: "그룹 변경",
  user_changed: "사용자 변경",
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
  password_changed: "비밀번호 변경",
  folder_changed: "폴더 변경",
  token_changed: "토큰 변경",
};

export const ACTIONS = Object.keys(ACTION_LABEL) as AuditAction[];

function text(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function visibilityLabel(value: unknown): string {
  return value === "public" || value === "private" ? VISIBILITY_LABEL[value as Visibility] : text(value);
}

/** 동작 칸의 이름. 버전·판 번호는 이름에 붙인다. */
export function actionName(entry: AuditRecord): string {
  const label = ACTION_LABEL[entry.action] ?? entry.action;
  if (entry.action === "text_updated") return `${label}(v${text(entry.detail.version)})`;
  if (entry.action === "original_replaced" || entry.action === "original_downloaded" || entry.action === "original_previewed") {
    return `${label}(${text(entry.detail.file_version)}판)`;
  }
  return label;
}

/** 동작에 덧붙는 설명 — 무엇이 어떻게 바뀌었나. 없으면 null. */
export function describeDetail(entry: AuditRecord): string | null {
  const { detail } = entry;
  if (entry.action === "share_changed") {
    const share = `「${text(detail.share_name)}」`;
    const descriptions: Record<string, string> = {
      created: `공유 ${share} 생성`,
      deleted: `공유 ${share} 삭제`,
      document_added: `${share}에 문서 추가`,
      document_removed: `${share}에서 문서 제거`,
      folder_added: `${share}에 폴더 「${text(detail.folder_name)}」 추가`,
      folder_removed: `${share}에서 폴더 「${text(detail.folder_name)}」 제거`,
      token_issued: `${share} 토큰 「${text(detail.token_name)}」 발급`,
      token_revoked: `${share} 토큰 「${text(detail.token_name)}」 폐기`,
    };
    const owner = text(detail.owner);
    return `${descriptions[text(detail.change)] ?? share}${owner && owner !== entry.actor ? ` (소유자 ${owner})` : ""}`;
  }
  if (entry.action === "user_changed" && detail.change === "department_changed") {
    const department = (value: unknown): string => (value === null || value === undefined ? "없음" : text(value));
    return `사용자 「${text(detail.user)}」 소속 부서: ${department(detail.from)} → ${department(detail.to)}`;
  }
  if (entry.action === "group_changed" && detail.change === "manager_added") return `부서 관리자 지정: ${text(detail.group)} ${text(detail.user)}`;
  if (entry.action === "group_changed" && detail.change === "manager_removed") return `부서 관리자 해제: ${text(detail.group)} ${text(detail.user)}`;
  if (entry.action === "group_changed" && detail.change === "description_changed") return `부서 설명 변경: ${text(detail.group)}`;
  if (entry.action === "group_changed" || entry.action === "user_changed") {
    const subject = entry.action === "group_changed" ? `그룹 ${text(detail.group)}` : `사용자 ${text(detail.user)}`;
    if (detail.change === "admin_granted") return `${subject} 관리자 권한 부여`;
    if (detail.change === "admin_revoked") return `${subject} 관리자 권한 회수`;
    if (detail.change === "deleted") return `${subject} 삭제`;
    return `${subject} 생성${detail.is_admin === true ? "(관리자)" : ""}`;
  }
  if (entry.action === "password_changed") return `사용자 ${text(detail.user)}`;
  if (entry.action === "folder_changed") {
    const folder = `「${text(detail.folder_name)}」`;
    if (detail.change === "renamed") return `폴더 「${text(detail.old_name)}」 → ${folder}`;
    if (detail.change === "department_set") return `폴더 ${folder} 부서 폴더 지정: ${text(detail.department)}`;
    if (detail.change === "department_cleared") return `폴더 ${folder} 부서 폴더 해제`;
    return `폴더 ${folder} ${detail.change === "deleted" ? "삭제" : "생성"}`;
  }
  if (entry.action === "token_changed") {
    const owner = text(detail.owner);
    const change = detail.change === "revoked" ? "폐기" : "발급";
    return `토큰 「${text(detail.token_name)}」 ${change}${owner && owner !== entry.actor ? ` (소유자 ${owner})` : ""}`;
  }
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

/** 앱 행위자가 없는 기록 경로의 이름. 이름이 없는 경로는 null. */
export function viaLabel(via: string | null | undefined): string | null {
  if (via === "worker") return "워커";
  if (via === "direct") return "직접 접속";
  if (via === "cli") return "운영자 CLI";
  return null;
}

/** 오늘은 로컬 자정, 최근 기간은 현재 시각부터 경과 일수 기준이다. */
export function auditSince(period: "today" | "7d" | "30d" | "all", now: Date): string | undefined {
  if (period === "all") return undefined;
  if (period === "today") {
    const midnight = new Date(now);
    midnight.setHours(0, 0, 0, 0);
    return midnight.toISOString();
  }
  const days = period === "7d" ? 7 : 30;
  return new Date(now.getTime() - days * 24 * 60 * 60 * 1000).toISOString();
}
