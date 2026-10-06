"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { GranteePicker } from "./GranteePicker";
import {
  ApiError,
  addShareDocument,
  getDocumentAccess,
  listPrincipals,
  listShares,
  removeShareDocument,
  setDocumentAccess,
} from "@/lib/api";
import { scopeLabel } from "@/lib/folders";
import {
  VISIBILITY_LABEL,
  type DocumentAccess,
  type FolderScope,
  type Principals,
  type ShareSummary,
  type Visibility,
} from "@/lib/types";

interface ShareChoice {
  id: string;
  name: string;
  included: boolean;
}

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
  const [followsFolder, setFollowsFolder] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [shares, setShares] = useState<ShareChoice[] | null>(null);
  const [sharesError, setSharesError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    // 공유는 열람 범위와 별개 축이다 — 실패해도 열람 범위 편집을 막지 않는다 (ADR-044 「공유」 결정 2).
    void listShares(controller.signal)
      .then((list: ShareSummary[]) => {
        if (controller.signal.aborted) return;
        setShares(
          list.map((share) => ({
            id: share.id,
            name: share.name,
            included: share.documents.some((document) => document.id === documentId),
          })),
        );
        setSharesError(null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setSharesError(reason instanceof ApiError ? reason.detail : "공유 목록을 불러오지 못했습니다.");
      });
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
        setFollowsFolder((access.folder != null || access.hidden_folder) && access.follows_folder);
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

  const folderScope = loaded?.folder != null ? loaded.folder_scope : null;
  // 볼 수 없게 된 폴더 안 문서도 폴더 안이다 — 이름·범위 없이 그 사실만 보인다 (ADR-054 D5).
  const inFolder = loaded !== null && (loaded.folder != null || loaded.hidden_folder);

  async function save(): Promise<void> {
    if (saving) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    // 조직 공개에는 부여 대상을 둘 수 없다 — 서버도 400으로 거부한다.
    const next: FolderScope =
      visibility === "public"
        ? { visibility, users: [], groups: [] }
        : { visibility, users, groups };
    // 폴더 밖 문서는 follows_folder를 보내지 않는다. 「폴더 범위 따름」에는 공개범위를 함께 보내면 서버가 400으로 거부한다.
    try {
      const saved = await setDocumentAccess(
        documentId,
        !inFolder ? next : followsFolder ? { followsFolder: true } : { ...next, followsFolder: false },
      );
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
  const sharedOutside = !(inFolder && followsFolder) && visibility === "public" && (shares ?? []).some((share) => share.included);

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
          {inFolder ? (
            <fieldset disabled={controlsDisabled}>
              <legend className="sr-only">폴더 범위 상속</legend>
              <div className="flex flex-wrap gap-4 text-sm text-neutral-300">
                <label className="flex items-center gap-2">
                  <input
                    checked={followsFolder}
                    name="document-inherit"
                    onChange={() => setFollowsFolder(true)}
                    type="radio"
                  />
                  폴더 범위 따름({folderScope !== null ? scopeLabel(folderScope) : "볼 수 없는 폴더"})
                </label>
                <label className="flex items-center gap-2">
                  <input
                    checked={!followsFolder}
                    name="document-inherit"
                    onChange={() => setFollowsFolder(false)}
                    type="radio"
                  />
                  개별 지정
                </label>
              </div>
              <p className="mt-2 text-sm text-neutral-500">
                {followsFolder
                  ? "폴더를 만든 사람이 범위를 바꾸면 이 문서에도 적용됩니다."
                  : "폴더 범위와 상관없이 이 문서만의 열람 범위를 씁니다."}
              </p>
            </fieldset>
          ) : null}
          {inFolder && followsFolder ? null : (
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
                  {clearsGrants ? " 저장하면 지정한 부여 대상도 함께 지워집니다. 외부 공유 포함은 유지됩니다." : ""}
                </p>
              )}
            </>
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
      <ShareToggles
        disabled={disabled || saving}
        documentId={documentId}
        error={sharesError}
        onChange={setShares}
        sharedOutside={sharedOutside}
        shares={shares}
      />
      {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}
      {message !== null ? <p className="text-sm text-neutral-400">{message}</p> : null}
    </section>
  );
}

/** 「내 공유에 포함」. 열람 범위 저장과 따로, 체크하는 즉시 반영한다. 실패하면 체크를 되돌린다. */
function ShareToggles({
  documentId,
  shares,
  error,
  disabled,
  sharedOutside,
  onChange,
}: {
  documentId: string;
  shares: ShareChoice[] | null;
  error: string | null;
  disabled: boolean;
  sharedOutside: boolean;
  onChange: (next: ShareChoice[]) => void;
}): React.ReactElement {
  const [pending, setPending] = useState<string | null>(null);
  const [toggleError, setToggleError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function toggle(share: ShareChoice): Promise<void> {
    if (shares === null || pending !== null) return;
    const include = !share.included;
    const withState = (included: boolean) =>
      shares.map((item) => (item.id === share.id ? { ...item, included } : item));
    setPending(share.id);
    setToggleError(null);
    setMessage(null);
    onChange(withState(include));
    try {
      if (include) await addShareDocument(share.id, documentId);
      else await removeShareDocument(share.id, documentId);
      setMessage(include ? `「${share.name}」 공유에 넣었습니다.` : `「${share.name}」 공유에서 뺐습니다.`);
    } catch (reason: unknown) {
      onChange(withState(share.included));
      setToggleError(reason instanceof ApiError ? reason.detail : "공유를 바꾸지 못했습니다.");
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium text-neutral-400">내 공유에 포함</h3>
      {error !== null ? (
        <p className="text-sm text-neutral-500">{error}</p>
      ) : shares === null ? (
        <p className="text-sm text-neutral-500">불러오는 중…</p>
      ) : shares.length === 0 ? (
        <p className="text-sm text-neutral-500">
          외부 공유가 없습니다. <Link className="text-[#0ea5e9] hover:underline" href="/settings">설정 화면</Link>에서 공유를 만드세요.
        </p>
      ) : (
        <fieldset disabled={disabled || pending !== null}>
          <legend className="sr-only">내 공유에 포함</legend>
          <div className="flex flex-wrap gap-4 text-sm text-neutral-300">
            {shares.map((share) => (
              <label className="flex items-center gap-2" key={share.id}>
                <input checked={share.included} onChange={() => void toggle(share)} type="checkbox" />
                {share.name}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      {sharedOutside ? (
        <p className="text-sm text-neutral-500">
          이 문서는 조직 공개와 별개로 외부 공유에 열려 있습니다. 공유 토큰을 가진 외부 주체도 읽습니다.
        </p>
      ) : null}
      {toggleError !== null ? <p className="text-sm text-[#ef4444]" role="alert">{toggleError}</p> : null}
      {message !== null ? <p className="text-sm text-neutral-400">{message}</p> : null}
    </div>
  );
}
