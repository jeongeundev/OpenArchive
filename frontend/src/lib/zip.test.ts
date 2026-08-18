import JSZip from "jszip";
import { describe, expect, it } from "vitest";

import { expandZip } from "./zip";

async function archiveFile(zip: JSZip, name = "documents.zip"): Promise<File> {
  const blob = await zip.generateAsync({ type: "blob" });
  return new File([blob], name, { type: "application/zip" });
}

describe("expandZip", () => {
  it("지원 문서만 File로 만들고 미지원 항목의 이름을 돌려준다", async () => {
    const zip = new JSZip();
    zip.file("report.pdf", "pdf");
    zip.file("draft.docx", "docx");
    zip.file("notes.txt", "txt");
    zip.file("GUIDE.MD", "markdown");
    zip.file("image.png", "png");
    zip.file("nested.zip", "zip");
    zip.file("README", "no extension");

    const result = await expandZip(await archiveFile(zip));

    expect(result.files.map((file) => file.name)).toEqual([
      "report.pdf",
      "draft.docx",
      "notes.txt",
      "GUIDE.MD",
    ]);
    expect(result.skipped).toEqual(["image.png", "nested.zip", "README"]);
    expect(result.files.every((file) => file instanceof File)).toBe(true);
    await expect(result.files[3].text()).resolves.toBe("markdown");
  });

  it("디렉터리와 macOS 메타데이터 및 숨김 파일은 결과에서 제외한다", async () => {
    const zip = new JSZip();
    zip.folder("empty");
    zip.file("__MACOSX/guide.md", "metadata");
    zip.file("docs/.DS_Store", "metadata");
    zip.file("docs/.draft.md", "hidden");
    zip.file(".hidden.txt", "hidden");
    zip.file("docs/guide.md", "guide");

    const result = await expandZip(await archiveFile(zip));

    expect(result.files.map((file) => file.name)).toEqual(["guide.md"]);
    expect(result.skipped).toEqual([]);
  });

  it("중첩 디렉터리의 지원 파일은 basename을 파일명으로 사용한다", async () => {
    const zip = new JSZip();
    zip.file("docs/guides/start.txt", "start");

    const result = await expandZip(await archiveFile(zip));

    expect(result.files[0].name).toBe("start.txt");
    await expect(result.files[0].text()).resolves.toBe("start");
  });

  it("손상된 ZIP은 예외를 던진다", async () => {
    const archive = new File([new Uint8Array([1, 2, 3, 4])], "broken.zip");

    await expect(expandZip(archive)).rejects.toThrow();
  });
});
