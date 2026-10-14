// Copyright 2026 jeongeundev
// SPDX-License-Identifier: MIT
"use client";

import { useId } from "react";
import { folderOptions } from "@/lib/folders";
import type { Folder } from "@/lib/types";

export function FolderSelect({ folders, value, onChange, label, noneLabel, disabled = false, id }: {
  folders: Folder[];
  value: string | null;
  onChange: (value: string | null) => void;
  label: string;
  noneLabel: string;
  disabled?: boolean;
  id?: string;
}) {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  return (
    <div>
      <label htmlFor={selectId} className="mb-2 block text-sm text-muted">{label}</label>
      <select id={selectId} value={value ?? ""} disabled={disabled}
        onChange={event => onChange(event.target.value || null)}
        className="w-full rounded-lg border border-line bg-card px-4 py-3 text-sm text-body disabled:opacity-50">
        <option value="">{noneLabel}</option>
        {folderOptions(folders).map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
      </select>
    </div>
  );
}
