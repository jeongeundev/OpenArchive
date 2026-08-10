const RELATION_LABELS: Record<string, string> = {
  overlaps: "전반적으로 같은 내용",
  points_to: "이 대목에서 만난다",
  related: "관련 있음",
  revision: "이전 텍스트 버전",
};

export function relationLabel(
  kind: string,
  perspective: "source" | "target" = "source",
): string {
  if (kind === "broader") {
    return perspective === "source" ? "더 자세한 문서" : "더 포괄적인 문서";
  }
  return RELATION_LABELS[kind] ?? "관련 있음";
}
