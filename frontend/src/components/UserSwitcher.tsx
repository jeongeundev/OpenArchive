"use client";

import { useEffect, useState } from "react";

import { DEMO_USERS, getCurrentUser, setCurrentUser } from "@/lib/user";

export function UserSwitcher(): React.ReactElement {
  const [currentUser, setDisplayedUser] = useState<string | null>(null);

  useEffect(() => {
    queueMicrotask(() => setDisplayedUser(getCurrentUser()));
  }, []);

  function handleChange(event: React.ChangeEvent<HTMLSelectElement>): void {
    const selectedUser = event.target.value || null;
    setCurrentUser(selectedUser);
    window.location.reload();
  }

  return (
    <label className="flex items-center gap-2 text-sm text-neutral-400">
      <span>데모 사용자</span>
      <select
        aria-label="데모 사용자"
        className="rounded-lg border border-neutral-800 bg-neutral-900 px-3 py-2 text-neutral-300"
        value={currentUser ?? ""}
        onChange={handleChange}
      >
        <option value="">익명(공개 문서만)</option>
        {DEMO_USERS.map((user) => (
          <option key={user} value={user}>
            {user}
          </option>
        ))}
      </select>
    </label>
  );
}
