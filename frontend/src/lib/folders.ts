import { getDocumentAccess, moveDocument } from "./api";
import { VISIBILITY_LABEL, type Folder, type FolderScope } from "./types";

export function folderChildren(folders: Folder[], parentId: string | null): Folder[] {
  return folders.filter(folder => folder.parent_id === parentId)
    .sort((a, b) => a.name.localeCompare(b.name, "ko"));
}

export function folderOptions(folders: Folder[]): { id: string; label: string; folder: Folder }[] {
  const ids = new Set(folders.map(folder => folder.id));
  const roots = folders.filter(folder => folder.parent_id === null || !ids.has(folder.parent_id))
    .sort((a, b) => a.name.localeCompare(b.name, "ko"));
  const options: { id: string; label: string; folder: Folder }[] = [];
  function visit(folder: Folder, prefix: string) {
    const label = prefix ? `${prefix}/${folder.name}` : folder.name;
    options.push({ id: folder.id, label, folder });
    for (const child of folderChildren(folders, folder.id)) visit(child, label);
  }
  for (const root of roots) visit(root, "");
  return options;
}

export function scopeLabel(scope: FolderScope): string {
  const label = VISIBILITY_LABEL[scope.visibility];
  const targets = [...scope.groups, ...scope.users];
  return scope.visibility === "private" && targets.length ? `${label} · ${targets.join(", ")}` : label;
}

export function sameScope(a: FolderScope, b: FolderScope): boolean {
  const equal = (left: string[], right: string[]) => {
    const sortedRight = [...right].sort();
    return left.length === right.length && [...left].sort().every((value, index) => value === sortedRight[index]);
  };
  return a.visibility === b.visibility && equal(a.users, b.users) && equal(a.groups, b.groups);
}

/**
 * 문서를 폴더로 옮긴다 (ADR-054). 「폴더 범위 따름」 문서의 실효 범위가 바뀔 때만 확인을 받고,
 * 취소하면 옮기지 않고 false를 돌려준다. 폴더 칸과 정리할 문서 화면이 같은 확인을 쓴다.
 */
export async function moveWithScopeConfirm(
  documentId: string, target: string | null, folders: Folder[],
): Promise<boolean> {
  // 목록에 없는 폴더는 범위를 알 수 없다 — 자기 범위로 간주하면 확인 없이 범위가 바뀐다.
  const destination = target === null ? null : folders.find(item => item.id === target) ?? null;
  if (target !== null && destination === null) {
    throw new Error("폴더 목록을 불러오지 못했습니다. 새로 고친 뒤 다시 시도해 주세요.");
  }
  const access = await getDocumentAccess(documentId);
  const own: FolderScope = { visibility: access.visibility, users: access.users, groups: access.groups };
  const before = access.folder !== null && access.follows_folder && access.folder_scope !== null
    ? access.folder_scope : own;
  const after = destination !== null && access.follows_folder ? destination.scope : own;
  // 숨은 폴더의 범위는 알 수 없다 — 따르는 중이면 늘 바뀌는 것으로 보고 확인을 받는다.
  const hiddenBefore = access.hidden_folder && access.follows_folder;
  const beforeLabel = hiddenBefore ? "볼 수 없는 폴더의 범위" : scopeLabel(before);
  if (access.follows_folder && (hiddenBefore || !sameScope(before, after))
    && !window.confirm(`열람 범위가 바뀝니다: 「${beforeLabel}」 → 「${scopeLabel(after)}」. 옮기시겠습니까?`)) {
    return false;
  }
  await moveDocument(documentId, target);
  return true;
}
