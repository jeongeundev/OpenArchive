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
    zip.file("공문.hwp", "hwp");
    zip.file("보고.HWPX", "hwpx");
    zip.file("예산.xlsx", "xlsx");
    zip.file("발표.PPTX", "pptx");
    zip.file("image.gif", "gif");
    zip.file("nested.zip", "zip");
    zip.file("README", "no extension");

    const result = await expandZip(await archiveFile(zip));

    expect(result.files.map((file) => file.name)).toEqual([
      "report.pdf",
      "draft.docx",
      "notes.txt",
      "GUIDE.MD",
      "공문.hwp",
      "보고.HWPX",
      "예산.xlsx",
      "발표.PPTX",
    ]);
    expect(result.skipped).toEqual(["image.gif", "nested.zip", "README"]);
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

  it("이미지(png·jpg·jpeg)도 지원 문서로 꺼낸다", async () => {
    const zip = new JSZip();
    zip.file("스캔.png", "png");
    zip.file("영수증.JPG", "jpg");
    zip.file("사진.jpeg", "jpeg");

    const { files, skipped } = await expandZip(await archiveFile(zip));

    expect(files.map((file) => file.name).sort()).toEqual(["사진.jpeg", "스캔.png", "영수증.JPG"]);
    expect(skipped).toEqual([]);
  });
});
