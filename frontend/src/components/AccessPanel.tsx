"use client";

import { useEffect, useState } from "react";

import { GranteePicker } from "./GranteePicker";
import { ApiError, getDocumentAccess, listPrincipals, setDocumentAccess } from "@/lib/api";
import { VISIBILITY_LABEL, type DocumentAccess, type Principals, type Visibility } from "@/lib/types";

/** 문서의 열람 범위를 보고 바꾼다. 부모가 소유자에게만 렌더한다 — 비소유자에게는 조회도 하지 않는다. */
export function AccessPanel({
  documentId,
  disabled = false,
  onSaved,
}: {
  documentId: string;
  disabled?: boolean;
  onSaved: () => void;
}): React.ReactElement {
  const [loaded, setLoaded] = useState<DocumentAccess | null>(null);
  const [principals, setPrincipals] = useState<Principals>({ users: [], groups: [] });
  const [visibility, setVisibility] = useState<Visibility>("public");
  const [users, setUsers] = useState<string[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      getDocumentAccess(documentId, controller.signal),
      listPrincipals(controller.signal),
    ])
      .then(([access, directory]) => {
        if (controller.signal.aborted) return;
        setLoaded(access);
        setVisibility(access.visibility);
        setUsers(access.users);
        setGroups(access.groups);
        setPrincipals(directory);
        setLoadError(null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setLoadError(
          reason instanceof ApiError ? reason.detail : "열람 범위를 불러오지 못했습니다.",
        );
      });
    return () => controller.abort();
  }, [documentId]);

  async function save(): Promise<void> {
    if (saving) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    // 조직 공개에는 부여 대상을 둘 수 없다 — 서버도 400으로 거부한다.
    const next: DocumentAccess =
      visibility === "public"
        ? { visibility, users: [], groups: [] }
        : { visibility, users, groups };
    try {
      const saved = await setDocumentAccess(documentId, next);
      setLoaded(saved);
      setUsers(saved.users);
      setGroups(saved.groups);
      setMessage("열람 범위를 저장했습니다.");
      onSaved();
    } catch (reason: unknown) {
      // 실패해도 고른 대상은 지우지 않는다 (TagEditor와 같은 원칙).
      setError(reason instanceof ApiError ? reason.detail : "열람 범위를 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  }

  const controlsDisabled = disabled || saving || loaded === null;
  const clearsGrants =
    visibility === "public" &&
    loaded !== null &&
    (loaded.users.length > 0 || loaded.groups.length > 0 || users.length > 0 || groups.length > 0);

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-medium text-neutral-400">열람 범위</h2>
      {loadError !== null ? (
        <p className="text-sm text-neutral-500">{loadError}</p>
      ) : loaded === null ? (
        <p className="text-sm text-neutral-500">불러오는 중…</p>
      ) : (
        <>
          <fieldset disabled={controlsDisabled}>
            <legend className="sr-only">열람 범위</legend>
            <div className="flex gap-4 text-sm text-neutral-300">
              {(["public", "private"] as const).map((value) => (
                <label className="flex items-center gap-2" key={value}>
                  <input
                    checked={visibility === value}
                    name="document-visibility"
                    onChange={() => setVisibility(value)}
                    type="radio"
                    value={value}
                  />
                  {VISIBILITY_LABEL[value]}
                </label>
              ))}
            </div>
          </fieldset>
          {visibility === "private" ? (
            <GranteePicker
              disabled={controlsDisabled}
              groups={groups}
              onChange={(next) => {
                setUsers(next.users);
                setGroups(next.groups);
              }}
              principals={principals}
              users={users}
            />
          ) : (
            <p className="text-sm text-neutral-500">
              조직 공개 문서는 로그인한 모든 사용자가 봅니다.
              {clearsGrants ? " 저장하면 지정한 부여 대상도 함께 지워집니다." : ""}
            </p>
          )}
          <button
            className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400"
            disabled={controlsDisabled}
            onClick={() => void save()}
            type="button"
          >
            {saving ? "저장 중…" : "열람 범위 저장"}
          </button>
        </>
      )}
      {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
      {message !== null ? <p className="text-sm text-neutral-400">{message}</p> : null}
    </section>
  );
}
