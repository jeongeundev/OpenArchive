// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import Link from "next/link";

/** 화면 오류의 경계. 오류 내용(message·digest)은 내부 사정이라 화면에 보이지 않는다. */
export default function ErrorPage({ retry }: {
  error: Error & { digest?: string };
  retry: () => void;
  reset: () => void;
}): React.ReactElement {
  return (
    <section className="space-y-4">
      <h1 className="text-4xl font-semibold text-white">화면을 표시하지 못했습니다</h1>
      <p className="text-sm text-neutral-400">잠시 뒤 다시 시도하세요. 문제가 계속되면 관리자에게 알려 주세요.</p>
      <div className="flex items-center gap-4">
        <button type="button" onClick={() => retry()}
          className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-black hover:bg-neutral-200">
          다시 시도
        </button>
        <Link href="/" className="text-sm text-[#0ea5e9] hover:underline">문서 목록으로 가기</Link>
      </div>
    </section>
  );
}
