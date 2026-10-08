/**
 * 한컴 HWP 형식 공개 조건이 UI에 적으라고 한 고지 (ADR-059 결정 1). 문구는 원문 그대로 둔다.
 */
export function SiteFooter(): React.ReactElement {
  return (
    <footer className="border-t border-neutral-800">
      <p className="mx-auto max-w-5xl px-6 py-4 text-xs text-neutral-500">
        본 제품은 한컴의 HWP 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
      </p>
    </footer>
  );
}
