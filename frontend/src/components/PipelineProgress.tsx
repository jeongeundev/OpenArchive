import type { DocumentProgress } from "@/lib/types";

// 문서별 배지(StatusBadge)와 같은 라벨이다 — 집계와 표가 같은 말을 쓴다.
const STAGES: { key: keyof DocumentProgress; label: string }[] = [
  { key: "extracting", label: "텍스트 인식 중" },
  { key: "extraction_failed", label: "텍스트 인식 실패" },
  { key: "pending", label: "대기 중" },
  { key: "processing", label: "처리 중…" },
  { key: "ready", label: "완료" },
  { key: "error", label: "실패" },
];

/** 보이는 문서가 파이프라인의 어느 단계에 몇 건 있는지. 페이지와 무관하게 전체를 센다. */
export function PipelineProgress({
  progress,
}: {
  progress: DocumentProgress;
}): React.ReactElement {
  const total = STAGES.reduce((sum, stage) => sum + progress[stage.key], 0);
  const inFlight = progress.extracting + progress.pending + progress.processing;

  return (
    <div className="space-y-1 text-sm">
      <p aria-label="처리 현황" className="text-neutral-300" role="status">
        <span className="font-medium text-white">문서 {total}건</span>
        {STAGES.filter((stage) => progress[stage.key] > 0).map((stage) => (
          <span className="text-neutral-400" key={stage.key}>
            {" · "}
            {stage.label} {progress[stage.key]}
          </span>
        ))}
      </p>
      {inFlight > 0 ? (
        <p className="text-xs text-neutral-500">
          처리 중인 문서는 워커가 임베딩을 마치면 검색에 나타납니다.
        </p>
      ) : null}
    </div>
  );
}
