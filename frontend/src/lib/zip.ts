import JSZip from "jszip";

import { SUPPORTED_CONTENT_TYPES } from "./types";

export type ExpandedZip = {
  files: File[];
  skipped: string[];
};

const supportedTypes = new Set<string>(SUPPORTED_CONTENT_TYPES);

export async function expandZip(archive: File): Promise<ExpandedZip> {
  const zip = await JSZip.loadAsync(archive);
  const files: File[] = [];
  const skipped: string[] = [];

  for (const entry of Object.values(zip.files)) {
    if (entry.dir) continue;

    const pathParts = entry.name.split("/");
    const basename = pathParts.at(-1) ?? entry.name;
    if (
      pathParts.includes("__MACOSX") ||
      pathParts.some((part) => part.startsWith("."))
    ) {
      continue;
    }

    const extension = basename.includes(".")
      ? basename.slice(basename.lastIndexOf(".") + 1).toLowerCase()
      : "";
    if (!supportedTypes.has(extension)) {
      skipped.push(entry.name);
      continue;
    }

    const content = await entry.async("blob");
    files.push(new File([content], basename));
  }

  return { files, skipped };
}
