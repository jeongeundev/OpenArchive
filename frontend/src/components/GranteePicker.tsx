"use client";

import { useState } from "react";

import type { Principals } from "@/lib/types";

export function GranteePicker({
  principals,
  users,
  groups,
  onChange,
  disabled = false,
}: {
  principals: Principals;
  users: string[];
  groups: string[];
  onChange: (next: { users: string[]; groups: string[] }) => void;
  disabled?: boolean;
}): React.ReactElement {
  const [userInput, setUserInput] = useState("");
  const [groupInput, setGroupInput] = useState("");

  const userCandidates = principals.users.filter((name) => !users.includes(name));
  const groupCandidates = principals.groups.filter((name) => !groups.includes(name));

  function addUser(): void {
    if (userInput === "" || users.includes(userInput)) return;
    onChange({ users: [...users, userInput], groups });
    setUserInput("");
  }

  function addGroup(): void {
    if (groupInput === "" || groups.includes(groupInput)) return;
    onChange({ users, groups: [...groups, groupInput] });
    setGroupInput("");
  }

  return (
    <div className="space-y-4 text-sm">
      <p className="text-neutral-500">
        선택한 사용자와 그룹 구성원만 이 문서를 봅니다. 대상을 고르지 않으면 소유자만 봅니다.
      </p>

      <div className="space-y-2">
        <ul aria-label="부여된 사용자" className="flex flex-wrap gap-2">
          {users.map((name) => (
            <li
              className="flex items-center gap-2 rounded bg-neutral-800 px-2 py-1 text-xs text-neutral-300"
              key={name}
            >
              <span className="text-neutral-500">사용자</span>
              {name}
              <button
                aria-label={`사용자 ${name} 제거`}
                className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
                disabled={disabled}
                onClick={() => onChange({ users: users.filter((item) => item !== name), groups })}
                type="button"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
        <div className="flex gap-3">
          <select
            aria-label="사용자 선택"
            className="rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-2 text-neutral-300 disabled:text-neutral-600"
            disabled={disabled}
            onChange={(event) => setUserInput(event.target.value)}
            value={userInput}
          >
            <option value="">사용자 선택</option>
            {userCandidates.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <button
            className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
            disabled={disabled || userInput === ""}
            onClick={addUser}
            type="button"
          >
            사용자 추가
          </button>
        </div>
      </div>

      <div className="space-y-2">
        <ul aria-label="부여된 그룹" className="flex flex-wrap gap-2">
          {groups.map((name) => (
            <li
              className="flex items-center gap-2 rounded bg-neutral-800 px-2 py-1 text-xs text-neutral-300"
              key={name}
            >
              <span className="text-neutral-500">그룹</span>
              {name}
              <button
                aria-label={`그룹 ${name} 제거`}
                className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
                disabled={disabled}
                onClick={() => onChange({ users, groups: groups.filter((item) => item !== name) })}
                type="button"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
        <div className="flex gap-3">
          <select
            aria-label="그룹 선택"
            className="rounded-lg border border-neutral-800 bg-neutral-900 px-4 py-2 text-neutral-300 disabled:text-neutral-600"
            disabled={disabled}
            onChange={(event) => setGroupInput(event.target.value)}
            value={groupInput}
          >
            <option value="">그룹 선택</option>
            {groupCandidates.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <button
            className="text-neutral-500 hover:text-neutral-300 disabled:text-neutral-600"
            disabled={disabled || groupInput === ""}
            onClick={addGroup}
            type="button"
          >
            그룹 추가
          </button>
        </div>
        <p className="text-neutral-500">
          그룹 구성원은 관리자가 변경할 수 있습니다. 관리자도 보면 안 되는 문서는 사용자에게 직접
          부여하세요.
        </p>
      </div>
    </div>
  );
}
