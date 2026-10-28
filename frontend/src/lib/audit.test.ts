// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import { describe, expect, it } from "vitest";

import { auditSince, ACTION_LABEL, actionName, describeDetail, viaLabel, type AuditRecord } from "./audit";

function record(overrides: Partial<AuditRecord>): AuditRecord {
  return { action: "document_created", actor: "alice", detail: {}, ...overrides };
}

describe("actionName", () => {
  it("동작 이름에 텍스트 버전과 원본 판 번호를 붙인다", () => {
    expect(actionName(record({ action: "document_created" }))).toBe("문서 생성");
    expect(actionName(record({ action: "text_updated", detail: { version: 2 } }))).toBe("텍스트 수정(v2)");
    expect(actionName(record({ action: "original_replaced", detail: { file_version: 2 } }))).toBe("원본 교체(2판)");
    expect(actionName(record({ action: "original_downloaded", detail: { file_version: 1 } }))).toBe("원본 내려받기(1판)");
    expect(actionName(record({ action: "original_previewed", detail: { file_version: 2 } }))).toBe("원본 미리보기(2판)");
  });

  it("모든 동작에 이름이 있다", () => {
    expect(ACTION_LABEL.document_trashed).toBe("휴지통 이동");
    expect(ACTION_LABEL.document_restored).toBe("복원");
    expect(ACTION_LABEL.document_deleted).toBe("영구 삭제");
  });
});

describe("describeDetail", () => {
  it("열람 범위 변경을 전→후로 적는다", () => {
    expect(describeDetail(record({ action: "access_changed", detail: { kind: "visibility", before: "public", after: "private" } }))).toBe("조직 공개 → 제한");
    expect(describeDetail(record({ action: "access_changed", detail: { kind: "inherit", before: "folder", after: "own" } }))).toBe("폴더 범위 따름 → 개별 지정");
  });

  it("폴더 이동을 적는다", () => {
    expect(describeDetail(record({ action: "access_changed", detail: { kind: "folder", before: "인사", after: "RFP" } }))).toBe("폴더 「인사」 → 「RFP」");
    expect(describeDetail(record({ action: "access_changed", detail: { kind: "folder", before: null, after: "RFP" } }))).toBe("폴더 없음 → 폴더 「RFP」");
  });

  it("부여 추가·제거를 적는다", () => {
    expect(describeDetail(record({ action: "access_changed", detail: { kind: "grant", change: "added", grantee_type: "user", grantee: "bob" } }))).toBe("사용자 bob 추가");
    expect(describeDetail(record({ action: "access_changed", detail: { kind: "grant", change: "removed", grantee_type: "group", grantee: "재무팀" } }))).toBe("그룹 재무팀 제거");
  });

  it("소유자 변경을 적는다", () => {
    expect(describeDetail(record({ action: "owner_changed", detail: { kind: "folder", folder_name: "RFP", before: "kim", after: "lee" } }))).toBe("폴더 「RFP」 kim → lee");
    expect(describeDetail(record({ action: "owner_changed", detail: { before: "kim", after: "lee" } }))).toBe("kim → lee");
  });

  it("외부 공유 변경은 행위자가 아닌 소유자를 덧붙인다", () => {
    expect(describeDetail(record({ action: "share_changed", actor: "admin", detail: { change: "document_added", share_name: "협업", owner: "kim" } }))).toBe("「협업」에 문서 추가 (소유자 kim)");
    expect(describeDetail(record({ action: "share_changed", actor: "kim", detail: { change: "created", share_name: "협업", owner: "kim" } }))).toBe("공유 「협업」 생성");
  });

  it("부서 관리자 지정·해제와 부서 설명 변경을 적는다", () => {
    expect(describeDetail(record({ action: "group_changed", detail: { change: "manager_added", group: "인사팀", user: "kim" } }))).toBe("부서 관리자 지정: 인사팀 kim");
    expect(describeDetail(record({ action: "group_changed", detail: { change: "manager_removed", group: "인사팀", user: "kim" } }))).toBe("부서 관리자 해제: 인사팀 kim");
    expect(describeDetail(record({ action: "group_changed", detail: { change: "description_changed", group: "인사팀" } }))).toBe("부서 설명 변경: 인사팀");
  });

  it("그룹 생성·삭제 문구는 그대로다", () => {
    expect(describeDetail(record({ action: "group_changed", detail: { change: "created", group: "인사팀" } }))).toBe("그룹 인사팀 생성");
    expect(describeDetail(record({ action: "group_changed", detail: { change: "deleted", group: "인사팀" } }))).toBe("그룹 인사팀 삭제");
  });

  it("설명이 없는 동작은 null", () => {
    expect(describeDetail(record({ action: "text_updated", detail: { version: 2 } }))).toBeNull();
    expect(describeDetail(record({ action: "original_replaced", detail: { file_version: 2 } }))).toBeNull();
  });
});

describe("viaLabel", () => {
  it("앱 행위자 없는 기록 경로를 이름으로 바꾼다", () => {
    expect(viaLabel("worker")).toBe("워커");
    expect(viaLabel("direct")).toBe("직접 접속");
    expect(viaLabel("cli")).toBe("운영자 CLI");
    expect(viaLabel("session")).toBeNull();
    expect(viaLabel(null)).toBeNull();
  });
});

describe("auditSince", () => {
  it("uses local midnight for today without changing now", () => {
    const now = new Date(2026, 9, 28, 23, 59, 59, 999);
    const original = now.getTime();
    expect(auditSince("today", now)).toBe(new Date(2026, 9, 28).toISOString());
    expect(now.getTime()).toBe(original);
    expect(auditSince("today", new Date(2026, 9, 28))).toBe(new Date(2026, 9, 28).toISOString());
  });

  it("subtracts rolling days across month and year boundaries", () => {
    const now = new Date("2026-01-03T12:34:56.789Z");
    expect(auditSince("7d", now)).toBe("2025-12-27T12:34:56.789Z");
    expect(auditSince("30d", now)).toBe("2025-12-04T12:34:56.789Z");
    expect(auditSince("all", now)).toBeUndefined();
  });
});
