import { Suspense } from "react";

import { DocumentDetailView } from "./DocumentDetailView";

/** 정적 export가 뽑아 두는 껍데기 경로. 서버가 모든 문서 ID 요청에 이 파일을 내려준다. */
export const FALLBACK_DOCUMENT_ID = "__id__";

// 정적 export는 빌드 시점에 아는 경로만 만드는데, 문서 ID는 실행 중에 생긴다. 껍데기
// 하나만 뽑아 두고 서버가 SPA fallback으로 재사용한다 (ADR-041).
export function generateStaticParams(): { id: string }[] {
  return [{ id: FALLBACK_DOCUMENT_ID }];
}

// 정적 export는 dynamicParams: true를 지원하지 않는다(Next 16 static-exports 문서).
// 껍데기 밖의 경로는 빌드가 만들지 않고, 서버가 fallback으로 채운다.
export const dynamicParams = false;

export default function DocumentDetailPage(): React.ReactElement {
  // useSearchParams(인용 위치)는 정적 export에서 Suspense 경계를 요구한다 — 없으면 빌드가 실패한다.
  return (
    <Suspense fallback={<p className="text-sm text-neutral-500">불러오는 중…</p>}>
      <DocumentDetailView />
    </Suspense>
  );
}
