// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { OwnerTransfer } from "@/components/OwnerTransfer";
import { AccessPanel } from "@/components/AccessPanel";
import { DocumentActions } from "@/components/DocumentActions";
import { DocumentActivity } from "@/components/DocumentActivity";
import { DocumentFolderSection } from "@/components/DocumentFolderSection";
import { DocumentHeader } from "@/components/DocumentHeader";
import { DocumentMeta } from "@/components/DocumentMeta";
import { OriginalFiles } from "@/components/OriginalFiles";
import { RelatedDocuments } from "@/components/RelatedDocuments";
import { TagEditor } from "@/components/TagEditor";
import { TagSuggestions } from "@/components/TagSuggestions";
import { TextEditor } from "@/components/TextEditor";
import { VersionHistory } from "@/components/VersionHistory";
import { useDocument } from "@/lib/useDocument";
import { useRelated } from "@/lib/useRelated";
import { lastListHref } from "@/lib/urlState";
import { ApiError, getDocumentBacklinks, getDocumentLinks, updateTags } from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { EXTRACTING_NOTICE, type Backlink, type ResolvedLink } from "@/lib/types";

/**
 * 답변 인용 링크 `?version=&chunk=` (ADR-043 결정 3). window.location이 아니라 라우터에서 읽는다 —
 * 검색 화면에서 오는 클라이언트 이동은 새 화면을 그린 뒤에 주소창을 바꾼다.
 */
function readVersionFocus(params: URLSearchParams): { version: number; chunk: number } | null {
  const version = Number(params.get("version"));
  const chunk = Number(params.get("chunk"));
  if (!params.has("chunk") || !Number.isInteger(version) || version < 1 || !Number.isInteger(chunk) || chunk < 0) {
    return null;
  }
  return { version, chunk };
}

type DetailTab = "text" | "versions" | "activity";

/** `?tab=versions|activity`. 본문은 쿼리 없이 둔다. 인용 위치(`?version=&chunk=`)가 있으면 버전 이력이 우선이다. */
function readTab(params: URLSearchParams, hasFocus: boolean, anonymous: boolean): DetailTab {
  if (hasFocus) return "versions";
  const tab = params.get("tab");
  if (tab === "versions") return "versions";
  if (tab === "activity" && !anonymous) return "activity";
  return "text";
}

function tabClass(active: boolean): string {
  return `-mb-px border-b-2 px-1 pb-2 text-sm ${
    active ? "border-accent font-medium text-strong" : "border-transparent text-muted hover:text-strong"
  }`;
}

export function DocumentDetailView(): React.ReactElement {
  // 정적 export에서는 빌드 시점의 껍데기 경로(FALLBACK_DOCUMENT_ID)가 params로 들어온다.
  // 서버가 어떤 ID 요청에든 같은 파일을 내려주므로, 실제 ID는 브라우저 URL에서 읽는다.
  const pathname = usePathname();
  const id = decodeURIComponent(pathname.split("/")[2] ?? "");
  const { document, loading, error, refresh } = useDocument(id);
  const relatedData = useRelated(id, document?.chunk_version ?? null);
  const [editing, setEditing] = useState(false);
  const [draftTags, setDraftTags] = useState<string[] | null>(null);
  const [savingTags, setSavingTags] = useState(false);
  const [tagError, setTagError] = useState<string | null>(null);
  const [links, setLinks] = useState<ResolvedLink[] | null>(null);
  const [backlinks, setBacklinks] = useState<Backlink[]>([]);
  const [linksError, setLinksError] = useState<string | null>(null);
  const searchParams = useSearchParams();
  const versionFocus = useMemo(() => readVersionFocus(new URLSearchParams(searchParams.toString())), [searchParams]);
  const { auth } = useAuth();
  const anonymous = !auth.authenticated;
  const router = useRouter();
  const urlTab = readTab(new URLSearchParams(searchParams.toString()), versionFocus !== null, anonymous);
  const [tab, setTab] = useState<DetailTab>(urlTab);
  // 주소가 바뀌면(다른 인용 링크로 이동 등) 그 탭으로 맞춘다. 탭 클릭은 주소를 같은 값으로 바꾸므로 그대로다.
  // 인용 위치는 값으로 비교한다 — 탭 클릭이 주소를 바꾸면 searchParams가 새 객체가 되어 객체 비교는 늘 달라진다.
  const focusKey = versionFocus === null ? null : `${versionFocus.version}:${versionFocus.chunk}`;
  const [shownUrlTab, setShownUrlTab] = useState({ tab: urlTab, focus: focusKey });
  if (shownUrlTab.tab !== urlTab || shownUrlTab.focus !== focusKey) {
    setShownUrlTab({ tab: urlTab, focus: focusKey });
    setTab(urlTab);
  }
  // 활동 기록은 처음 열 때 불러오고, 그 뒤로는 숨겨도 마운트를 유지한다.
  const [activityOpened, setActivityOpened] = useState(urlTab === "activity");
  if (tab === "activity" && !activityOpened) setActivityOpened(true);

  function selectTab(next: DetailTab) {
    setTab(next);
    const params = new URLSearchParams(searchParams.toString());
    if (next === "text") params.delete("tab");
    else params.set("tab", next);
    const query = params.toString();
    router.replace(query === "" ? pathname : `${pathname}?${query}`, { scroll: false });
  }

  // 열람 범위는 소유자만 본다. 비소유자에게는 조회도 하지 않는다 — 패널 자리가 제한의 존재를 드러낸다.
  const isOwner = auth.authenticated && document !== null && auth.username === document.owner_id;

  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      getDocumentLinks(id, controller.signal),
      getDocumentBacklinks(id, controller.signal),
    ])
      .then(([nextLinks, nextBacklinks]) => {
        if (controller.signal.aborted) return;
        setLinks(nextLinks);
        setBacklinks(nextBacklinks);
        setLinksError(null);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        // links를 []로 두면 본문의 모든 링크가 깨진 링크로 보인다. 해석 결과가 없다는
        // 것을 그대로 남기고 실패를 말한다.
        setLinks(null);
        setBacklinks([]);
        setLinksError("문서 링크를 불러오지 못했습니다.");
      });
    return () => controller.abort();
  }, [id, document?.version]);

  if (loading) return <p className="text-sm text-muted">불러오는 중…</p>;
  if (document === null) {
    return <p className="text-sm text-muted">{error ?? "문서를 찾을 수 없습니다."}</p>;
  }
  // 화면에 보이는 태그 목록. 저장 전 편집분이 있으면 그쪽이 기준이다 — 추천 적용도
  // 이 목록 위에 얹어야 편집 중이던 태그가 조용히 사라지지 않는다.
  const tags = draftTags ?? document.tags;
  // 폴더가 바뀌면(숨은 폴더에서 빠질 때 포함) 폴더 영역과 열람 범위 패널을 새로 그린다.
  const folderKey = document.folder?.id ?? (document.hidden_folder ? "hidden" : "none");

  // 태그 저장은 여기 한 곳에서만 한다. 편집 저장과 추천 적용이 각자 updateTags를
  // 부르면 서로 다른 목록을 기준으로 삼게 된다.
  async function saveTags(next: string[]): Promise<void> {
    if (savingTags) return;
    setSavingTags(true);
    setTagError(null);
    try {
      await updateTags(id, next);
      setDraftTags(next);
      refresh();
      relatedData.refresh();
    } catch (reason: unknown) {
      // 실패해도 편집 중이던 태그는 지우지 않는다 (TextEditor의 409 처리와 같은 원칙).
      setTagError(
        reason instanceof ApiError ? reason.detail : "태그를 저장하지 못했습니다.",
      );
    } finally {
      setSavingTags(false);
    }
  }

  return (
    <div className="space-y-8">
      {/* 마지막으로 본 목록의 폴더·조건·쪽으로 돌아간다. 문서는 불러온 뒤에만 그리므로 서버 프리렌더와 어긋나지 않는다. */}
      <Link href={lastListHref()} className="text-sm text-muted hover:text-strong">
        ← 문서 목록
      </Link>

      <DocumentHeader document={document} />

      <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_340px]" data-testid="document-columns">
        <div className="min-w-0 space-y-6 rounded-xl border border-line bg-card p-6">
          <div className="flex gap-6 border-b border-line" role="tablist" aria-label="문서 내용">
            <button
              type="button"
              role="tab"
              id="document-tab-text"
              aria-selected={tab === "text"}
              aria-controls="document-panel-text"
              className={tabClass(tab === "text")}
              onClick={() => selectTab("text")}
            >
              본문
            </button>
            <button
              type="button"
              role="tab"
              id="document-tab-versions"
              aria-selected={tab === "versions"}
              aria-controls="document-panel-versions"
              className={tabClass(tab === "versions")}
              onClick={() => selectTab("versions")}
            >
              버전 이력{" "}
              <span className="ml-1 rounded-full bg-sunken px-1.5 py-0.5 text-xs text-muted">
                {document.versions.length}
              </span>
            </button>
            {anonymous ? null : (
              <button
                type="button"
                role="tab"
                id="document-tab-activity"
                aria-selected={tab === "activity"}
                aria-controls="document-panel-activity"
                className={tabClass(tab === "activity")}
                onClick={() => selectTab("activity")}
              >
                활동 기록
              </button>
            )}
          </div>

          {/* 본문·버전 이력은 숨기기만 한다 — 편집 중 내용과 펼친 버전을 탭을 오가도 잃지 않는다. */}
          <div
            className="space-y-8"
            role="tabpanel"
            id="document-panel-text"
            aria-labelledby="document-tab-text"
            hidden={tab !== "text"}
          >
            <TextEditor
              disabled={anonymous}
              document={document}
              onEditingChange={setEditing}
              onSaved={refresh}
              links={links}
            />

            {linksError !== null ? (
              <p className="text-sm text-muted" role="status">
                {linksError}
              </p>
            ) : null}

            {backlinks.length > 0 ? (
              <section className="space-y-4">
                <h2 className="text-sm font-medium text-muted">
                  이 문서를 가리키는 문서
                </h2>
                <ul className="space-y-2">
                  {backlinks.map((backlink) => (
                    <li key={backlink.document_id}>
                      <Link
                        className="text-sm text-accent hover:underline"
                        href={`/documents/${backlink.document_id}`}
                      >
                        {backlink.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </div>

          <div
            role="tabpanel"
            id="document-panel-versions"
            aria-labelledby="document-tab-versions"
            hidden={tab !== "versions"}
          >
            <VersionHistory
              documentId={document.id}
              versions={document.versions}
              currentVersion={document.version}
              disabled={anonymous}
              focus={versionFocus}
              restoreBlockedReason={
                document.extraction_status === "pending" ? EXTRACTING_NOTICE : null
              }
              onRestored={refresh}
            />
          </div>

          {!anonymous && activityOpened ? (
            <div
              role="tabpanel"
              id="document-panel-activity"
              aria-labelledby="document-tab-activity"
              hidden={tab !== "activity"}
            >
              {/* 복원·저장으로 버전이 바뀌면 새로 불러온다. */}
              <DocumentActivity key={`${document.id}:${document.version}`} documentId={document.id} />
            </div>
          ) : null}
        </div>

        <aside className="min-w-0 space-y-6" data-testid="document-side">
          {/* 자주 쓰지 않는 관리 동작은 접어 기둥 맨 위에 둔다 — 본문 아래면 긴 문서에서 닿지 않는다. 임베딩 오류 문서는 재임베딩 버튼이 보이도록 펼친다. */}
          {!anonymous ? (
            <details className="rounded-xl border border-line bg-card p-5" open={document.embedding_status === "error"}>
              <summary className="cursor-pointer text-sm font-semibold text-strong hover:text-accent">문서 관리</summary>
              <div className="mt-4 space-y-8">
                <DocumentActions disabled={editing} document={document} onChanged={refresh} />
                {isOwner ? (
                  <>
                    <AccessPanel key={folderKey} disabled={editing} documentId={document.id} onSaved={refresh} />
                    <OwnerTransfer documentId={document.id} owner={document.owner_id} disabled={editing} onTransferred={refresh} />
                  </>
                ) : null}
              </div>
            </details>
          ) : null}

          <DocumentFolderSection
            key={folderKey}
            disabled={editing}
            documentId={document.id}
            folder={document.folder}
            hiddenFolder={document.hidden_folder ?? false}
            isOwner={isOwner}
            onMoved={refresh}
          />

          {relatedData.related !== null ? (
            <RelatedDocuments
              response={relatedData.related}
              textMissing={document.extraction_status === "failed"}
            />
          ) : (
            <section className="space-y-3 rounded-xl border border-line bg-card p-5">
              <h2 className="text-sm font-semibold text-strong">관련 문서</h2>
              <p className="text-sm text-muted">
                {relatedData.loading ? "불러오는 중…" : "관련 문서를 불러오지 못했습니다."}
              </p>
            </section>
          )}

          <OriginalFiles
            anonymous={anonymous}
            disabled={editing}
            document={document}
            onChanged={refresh}
          />

          {!anonymous ? (
            <TagEditor disabled={editing} error={tagError} onChange={setDraftTags} onSave={() => void saveTags(tags)} saving={savingTags} tags={tags} />
          ) : null}

          {relatedData.suggestions !== null ? (
            <TagSuggestions
              onApply={
                anonymous || editing || savingTags
                  ? undefined
                  : (tag) => saveTags([...tags, tag])
              }
              response={relatedData.suggestions}
              textMissing={document.extraction_status === "failed"}
            />
          ) : (
            <section className="space-y-3 rounded-xl border border-line bg-card p-5">
              <h2 className="text-sm font-semibold text-strong">태그 추천</h2>
              <p className="text-sm text-muted">
                {relatedData.loading ? "불러오는 중…" : "태그 추천을 불러오지 못했습니다."}
              </p>
            </section>
          )}

          {relatedData.error !== null ? (
            <p className="text-sm text-muted" role="status">
              {relatedData.error}
            </p>
          ) : null}

          <DocumentMeta document={document} />
        </aside>
      </div>

      {error !== null ? (
        <p className="text-sm text-muted" role="status">
          {error}
        </p>
      ) : null}
    </div>
  );
}
