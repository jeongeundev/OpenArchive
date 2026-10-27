// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
import Link from "next/link";
import { Fragment } from "react";

import type { OwnerDepartment } from "@/lib/types";

/**
 * 소유자 표시 — `이름(부서)` (D16). 부서 폴더가 보일 때만 부서가 그 폴더 링크다.
 * 볼 수 없으면 `folder_id`가 null로 오고, 링크·자물쇠 없이 글자만 둔다 (ADR-054 D5).
 */
export function OwnerLabel({
  owner,
  department,
}: {
  owner: string;
  department?: OwnerDepartment | null;
}): React.ReactElement {
  if (!department) return <>{owner}</>;
  return (
    <>
      {owner}(
      {department.folder_id === null ? (
        department.name
      ) : (
        <Link href={`/?folder=${encodeURIComponent(department.folder_id)}`} className="hover:text-accent">
          {department.name}
        </Link>
      )}
      )
    </>
  );
}

/** 메타 줄 — 빈 값은 빼고 ` · `로 잇는다. 소유자처럼 링크가 든 칸도 섞을 수 있다. */
export function joinMeta(parts: React.ReactNode[]): React.ReactNode {
  return parts
    .filter((part) => part !== null && part !== undefined && part !== false && part !== "")
    .map((part, index) => (
      <Fragment key={index}>
        {index > 0 ? " · " : null}
        {part}
      </Fragment>
    ));
}
