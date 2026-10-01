"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  ApiError,
  createShare,
  createShareToken,
  deleteShare,
  listShares,
  removeShareDocument,
  revokeShareToken,
} from "@/lib/api";
import type { ShareSummary, ShareTokenCreated } from "@/lib/types";
import { useUnmountSignal } from "@/lib/useUnmountSignal";

function detail(reason: unknown, fallback: string): string {
  return reason instanceof ApiError ? reason.detail : fallback;
}

/** `/settings`의 「외부 공유」 절. 공유에 문서를 넣는 일은 문서 상세의 열람 범위 패널이 한다. */
export function SharesSection(): React.ReactElement {
  const [shares, setShares] = useState<ShareSummary[] | null>(null);
  const [name, setName] = useState("");
  const [tokenNames, setTokenNames] = useState<Record<string, string>>({});
  const [issued, setIssued] = useState<{ shareId: string; token: ShareTokenCreated } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const unmountSignal = useUnmountSignal();

  useEffect(() => {
    const controller = new AbortController();
    listShares(controller.signal)
      .then((items) => {
        if (!controller.signal.aborted) setShares(items);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(detail(reason, "공유 목록을 불러오지 못했습니다."));
        }
      });
    return () => controller.abort();
  }, []);

  /** 동작 하나를 실행하고 목록을 다시 받는다. 원문 토큰은 발급 직후 한 번만 보인다. */
  async function run(action: () => Promise<void>, fallback: string): Promise<void> {
    if (working) return;
    setWorking(true);
    setError(null);
    setIssued(null);
    try {
      await action();
      setShares(await listShares(unmountSignal()));
    } catch (reason: unknown) {
      setError(detail(reason, fallback));
    } finally {
      setWorking(false);
    }
  }

  function create(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void run(async () => {
      await createShare(name);
      setName("");
    }, "공유를 만들지 못했습니다.");
  }

  function issue(event: React.FormEvent<HTMLFormElement>, share: ShareSummary): void {
    event.preventDefault();
    void run(async () => {
      const token = await createShareToken(share.id, tokenNames[share.id] ?? "");
      setTokenNames((current) => ({ ...current, [share.id]: "" }));
      // run이 시작할 때 원문을 지우므로, 발급 결과는 동작 안에서 세운다.
      setIssued({ shareId: share.id, token });
    }, "공유 토큰을 발급하지 못했습니다.");
  }

  function remove(share: ShareSummary): void {
    if (
      !window.confirm(
        `'${share.name}' 공유를 삭제하시겠습니까? 이 공유의 토큰이 모두 무효가 되고, 외부에서 더 이상 볼 수 없습니다. 공유에 넣은 문서 자체는 지워지지 않습니다.`,
      )
    ) {
      return;
    }
    void run(() => deleteShare(share.id), "공유를 삭제하지 못했습니다.");
  }

  function revoke(share: ShareSummary, tokenId: string, tokenName: string): void {
    if (!window.confirm(`'${tokenName}' 토큰을 폐기하시겠습니까? 이 토큰을 쓰는 프로그램은 즉시 401을 받습니다.`)) {
      return;
    }
    void run(() => revokeShareToken(share.id, tokenId), "공유 토큰을 폐기하지 못했습니다.");
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-medium text-neutral-400">외부 공유</h2>
        <p className="mt-2 text-sm text-neutral-400">
          계정이 없는 외부 협업자에게 문서 일부를 엽니다. 공유 토큰은 읽기 전용이며, 그 토큰으로는
          공유에 넣은 문서만 보이고 나머지 문서는 존재하지 않는 것처럼 보입니다. 접속은 REST API로
          합니다.
        </p>
        <p className="mt-2 text-sm text-neutral-400">
          조직 공개 문서도 공유에 넣으면 외부에 열립니다. 문서는 문서 상세의 「열람 범위」 패널에서
          공유에 넣습니다.
        </p>
      </div>

      <form
        className="flex flex-wrap items-end gap-4 rounded-lg border border-neutral-800 bg-[#141414] p-6"
        onSubmit={create}
      >
        <label className="flex-1 text-sm text-neutral-400">공유 이름
          <input
            className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300"
            onChange={(event) => setName(event.target.value)}
            placeholder="B사 협업"
            required
            type="text"
            value={name}
          />
        </label>
        <button
          className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400"
          disabled={working}
          type="submit"
        >
          공유 만들기
        </button>
      </form>

      {error !== null ? <p className="text-sm text-[#ef4444]" role="alert">{error}</p> : null}

      {shares !== null && shares.length === 0 ? (
        <p className="text-sm text-neutral-500">아직 공유가 없습니다.</p>
      ) : null}

      {(shares ?? []).map((share) => {
        const headingId = `share-${share.id}`;
        return (
          <section
            aria-labelledby={headingId}
            className="space-y-4 rounded-lg border border-neutral-800 bg-[#141414] p-6"
            key={share.id}
          >
            <div className="flex items-start justify-between gap-4">
              <h3 className="text-sm font-medium text-white" id={headingId}>{share.name}</h3>
              <button
                className="text-sm text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
                disabled={working}
                onClick={() => remove(share)}
                type="button"
              >
                공유 삭제
              </button>
            </div>

            <div>
              <p className="text-xs text-neutral-500">포함 문서</p>
              {share.documents.length === 0 ? (
                <p className="mt-2 text-sm text-neutral-500">아직 넣은 문서가 없습니다.</p>
              ) : (
                <ul className="mt-2 space-y-1">
                  {share.documents.map((document) => (
                    <li className="flex items-center justify-between gap-4 text-sm" key={document.id}>
                      <Link className="text-[#0ea5e9] hover:underline" href={`/documents/${document.id}`}>
                        {document.title}
                      </Link>
                      <button
                        aria-label={`${document.title} 빼기`}
                        className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
                        disabled={working}
                        onClick={() =>
                          void run(
                            () => removeShareDocument(share.id, document.id),
                            "문서를 공유에서 빼지 못했습니다.",
                          )
                        }
                        type="button"
                      >
                        빼기
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <p className="text-xs text-neutral-500">공유 토큰</p>
              {share.tokens.length === 0 ? (
                <p className="mt-2 text-sm text-neutral-500">발급한 토큰이 없습니다.</p>
              ) : (
                <ul className="mt-2 space-y-1">
                  {share.tokens.map((token) => (
                    <li className="flex items-center justify-between gap-4 text-sm" key={token.id}>
                      <span className="text-neutral-300">{token.name}</span>
                      <button
                        aria-label={`${token.name} 폐기`}
                        className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
                        disabled={working}
                        onClick={() => revoke(share, token.id, token.name)}
                        type="button"
                      >
                        폐기
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              {issued?.shareId === share.id ? (
                <div className="mt-3 space-y-2 rounded-lg border border-[#0ea5e9] p-4" role="status">
                  <p className="text-sm text-white">
                    &lsquo;{issued.token.name}&rsquo; 공유 토큰을 발급했습니다. 지금 복사해 협업자에게
                    전달하세요 — <strong className="font-semibold">이 값은 다시 볼 수 없습니다.</strong>
                  </p>
                  <code className="block break-all rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 font-mono text-sm text-neutral-200">
                    {issued.token.token}
                  </code>
                </div>
              ) : null}

              <form className="mt-3 flex flex-wrap items-end gap-4" onSubmit={(event) => issue(event, share)}>
                <label className="flex-1 text-sm text-neutral-400">토큰 이름
                  <input
                    className="mt-2 w-full rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-3 text-neutral-300"
                    onChange={(event) =>
                      setTokenNames((current) => ({ ...current, [share.id]: event.target.value }))
                    }
                    placeholder="B사 연동"
                    required
                    type="text"
                    value={tokenNames[share.id] ?? ""}
                  />
                </label>
                <button
                  className="rounded-lg bg-white px-4 py-2 text-sm text-black hover:bg-neutral-200 disabled:bg-neutral-700 disabled:text-neutral-400"
                  disabled={working}
                  type="submit"
                >
                  공유 토큰 발급
                </button>
              </form>
            </div>
          </section>
        );
      })}
    </div>
  );
}
