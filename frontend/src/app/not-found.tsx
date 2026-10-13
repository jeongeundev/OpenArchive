// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import Link from "next/link";

export default function NotFound(): React.ReactElement {
  return (
    <section className="space-y-4">
      <h1 className="text-4xl font-semibold text-white">페이지를 찾을 수 없습니다</h1>
      <p className="text-sm text-neutral-400">주소가 잘못되었거나 없는 화면입니다.</p>
      <Link href="/" className="inline-block text-sm text-[#0ea5e9] hover:underline">문서 목록으로 가기</Link>
    </section>
  );
}
