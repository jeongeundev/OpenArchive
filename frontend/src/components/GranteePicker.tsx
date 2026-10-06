"use client";

import { useState } from "react";

import type { Principals } from "@/lib/types";

export function GranteePicker({
  principals,
  users,
  groups,
  onChange,
  disabled = false,
  description = "선택한 사용자와 그룹 구성원만 이 문서를 봅니다. 대상을 고르지 않으면 소유자만 봅니다.",
}: {
  principals: Principals;
  users: string[];
  groups: string[];
  onChange: (next: { users: string[]; groups: string[] }) => void;
  disabled?: boolean;
  /** 대상 선택 안내. 문서가 기본이고, 폴더 범위 패널은 폴더용 문구를 넘긴다. */
  description?: string;
}): React.ReactElement {
  return (
    <div className="space-y-4 text-sm">
      <p className="text-neutral-500">{description}</p>

      <GranteeList
        candidates={principals.users}
        disabled={disabled}
        label="사용자"
        onChange={(next) => onChange({ users: next, groups })}
        selected={users}
      />

      <div className="space-y-2">
        <GranteeList
          candidates={principals.groups}
          disabled={disabled}
          label="그룹"
          onChange={(next) => onChange({ users, groups: next })}
          selected={groups}
        />
        <p className="text-neutral-500">
          그룹 구성원은 관리자가 변경할 수 있습니다. 관리자도 보면 안 되는 문서는 사용자에게 직접
          부여하세요.
        </p>
      </div>
    </div>
  );
}

/** 한 종류(사용자 또는 그룹)의 선택된 대상 칩과 후보 select. */
function GranteeList({
  label,
  candidates,
  selected,
  onChange,
  disabled,
}: {
  label: "사용자" | "그룹";
  candidates: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  disabled: boolean;
}): React.ReactElement {
  const [input, setInput] = useState("");
  const remaining = candidates.filter((name) => !selected.includes(name));

  function add(): void {
    if (input === "" || selected.includes(input)) return;
    onChange([...selected, input]);
    setInput("");
  }

  return (
    <div className="space-y-2">
      <ul aria-label={`부여된 ${label}`} className="flex flex-wrap gap-2">
        {selected.map((name) => (
          <li
            className="flex items-center gap-2 rounded bg-neutral-800 px-2 py-1 text-xs text-neutral-300"
            key={name}
          >
            <span className="text-neutral-500">{label}</span>
            {name}
            <button
              aria-label={`${label} ${name} 제거`}
              className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
              disabled={disabled}
              onClick={() => onChange(selected.filter((item) => item !== name))}
              type="button"
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="flex gap-3">
        <select
          aria-label={`${label} 선택`}
          className="rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-2 text-neutral-300 disabled:text-neutral-600"
          disabled={disabled}
          onChange={(event) => setInput(event.target.value)}
          value={input}
        >
          <option value="">{label} 선택</option>
          {remaining.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <button
          className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
          disabled={disabled || input === ""}
          onClick={add}
          type="button"
        >
          {label} 추가
        </button>
      </div>
    </div>
  );
}
