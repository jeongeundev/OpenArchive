// overlaps는 "같은 내용"이 아니라 "자기 대목 대부분이 상대 문서에서 최근접 이웃을 찾았다"이다.
// 이웃 판정이 순위 기반이라 절대 거리 임계가 없고, 주제가 가까운 문서끼리는 모든 대목이 서로
// 최근접이 되어 비율이 1.0에 붙는다. 실 BGE-M3 실측에서 `PRD`↔`UI 디자인 가이드`,
// `ADR-020`↔`고가용성(HA) 전략`이 모두 1.00으로 나왔다 (OPENSQL_RESEARCH §14).
// 그래서 어휘를 `points_to`("이 대목에서")와 같은 축에 두고 대목 수만 달리 말한다.
const RELATION_LABELS: Record<string, string> = {
  overlaps: "여러 대목에서 만난다",
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
