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
