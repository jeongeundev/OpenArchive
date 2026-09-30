/** 볼 수 있는 문서가 하나도 없을 때의 첫 화면. 올리는 길과 예제를 넣는 길을 함께 보인다. */
export function EmptyDocuments({ username }: { username: string }): React.ReactElement {
  return (
    <div className="space-y-3 rounded-lg border border-neutral-800 bg-[#141414] px-6 py-8 text-sm">
      <p className="font-medium text-white">아직 문서가 없습니다.</p>
      <p className="text-neutral-400">
        위에서 파일을 올리거나, 서버에서 아래 명령으로 예제 64건(가상 회사의 사내 규정)을 넣어
        검색과 관계를 바로 살펴볼 수 있습니다.
      </p>
      <code className="block rounded bg-neutral-900 px-3 py-2 font-mono text-neutral-200">
        openarchive demo --user {username}
      </code>
    </div>
  );
}
