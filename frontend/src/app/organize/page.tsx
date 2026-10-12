"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, listUnfiled } from "@/lib/api";
import { moveWithScopeConfirm } from "@/lib/folders";
import { useFolders } from "@/lib/useFolders";
import type { UnfiledDocument, UnfiledResponse } from "@/lib/types";

/**
 * 정리할 문서 (ADR-064) — 폴더 밖에 있는 내 문서를 관계 이웃의 과반이 든 폴더와 함께 보인다.
 * 옮기는 것은 사람이 누를 때뿐이고, 열람 범위가 바뀌면 폴더 칸과 같은 확인을 받는다.
 */
export default function OrganizePage(): React.ReactElement {
  // 폴더 범위를 알아야 범위 변경 확인을 할 수 있다 — 폴더를 불러오기 전에는 옮기기를 막는다.
  const { folders, loading: foldersLoading, refresh: refreshFolders } = useFolders(true);
  const [result, setResult] = useState<UnfiledResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [workingId, setWorkingId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    listUnfiled(controller.signal).then((loaded) => {
      if (!controller.signal.aborted) setResult(loaded);
    }).catch((reason: unknown) => {
      if (controller.signal.aborted) return;
      setLoadError(reason instanceof ApiError ? reason.detail : "정리할 문서를 불러오지 못했습니다.");
    });
    return () => controller.abort();
  }, []);

  async function apply(item: UnfiledDocument, folderId: string): Promise<void> {
    setWorkingId(item.document_id);
    setActionError(null);
    try {
      if (!await moveWithScopeConfirm(item.document_id, folderId, folders)) return;
      setResult((current) => current === null ? current : {
        count: current.count - 1,
        items: current.items.filter((each) => each.document_id !== item.document_id),
      });
      refreshFolders();
    } catch (reason: unknown) {
      setActionError(reason instanceof ApiError ? reason.detail : "폴더를 옮기지 못했습니다.");
    } finally {
      setWorkingId(null);
    }
  }

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-4xl font-semibold text-white">정리할 문서</h1>
        <p className="mt-3 text-sm text-neutral-400">
          폴더 밖에 있는 내 문서입니다. 관련 문서의 절반 이상이 한 폴더에 있으면 그 폴더를 추천합니다. 자동으로 옮기지는 않습니다.
        </p>
      </div>

      {actionError !== null ? <p className="text-sm text-[#ef4444]" role="alert">{actionError}</p> : null}

      {loadError !== null ? (
        <p className="text-sm text-neutral-500" role="status">{loadError}</p>
      ) : result === null ? (
        <p className="text-sm text-neutral-500">불러오는 중…</p>
      ) : result.items.length === 0 ? (
        <p className="text-sm text-neutral-500">폴더 밖에 있는 내 문서가 없습니다.</p>
      ) : (
        <>
          {result.count > result.items.length ? (
            <p className="text-sm text-neutral-500">
              {`폴더 밖 문서 ${result.count}건 중 최근 ${result.items.length}건입니다.`}
            </p>
          ) : null}
          <div className="overflow-x-auto rounded-lg border border-neutral-800 bg-[#141414]">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-neutral-800 text-neutral-400">
                <tr>
                  <th className="px-4 py-3 font-medium">제목</th>
                  <th className="px-4 py-3 font-medium">추천 폴더</th>
                  <th className="px-4 py-3 font-medium">근거</th>
                  <th className="px-4 py-3 font-medium" />
                </tr>
              </thead>
              <tbody>
                {result.items.map((item) => {
                  const label = item.suggestion?.path.map((each) => each.name).join("/") ?? null;
                  return (
                    <tr className="border-b border-neutral-800 last:border-0" key={item.document_id}>
                      <td className="px-4 py-3">
                        <Link className="text-white hover:text-[#0ea5e9]" href={`/documents/${item.document_id}`}>
                          {item.title}
                        </Link>
                      </td>
                      {item.suggestion === null ? (
                        <td className="px-4 py-3 text-neutral-500" colSpan={3}>추천할 폴더가 없습니다</td>
                      ) : (
                        <>
                          <td className="px-4 py-3 text-neutral-300">{label}</td>
                          <td className="px-4 py-3 text-neutral-500">
                            {`관련 문서 ${item.suggestion.total}개 중 ${item.suggestion.votes}개`}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex justify-end">
                              <button
                                className="text-sm text-[#0ea5e9] hover:underline disabled:cursor-not-allowed disabled:text-neutral-600"
                                disabled={workingId !== null || foldersLoading}
                                onClick={() => void apply(item, item.suggestion!.folder_id)}
                                type="button"
                              >
                                {`${label} 폴더로 옮기기`}
                              </button>
                            </div>
                          </td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
