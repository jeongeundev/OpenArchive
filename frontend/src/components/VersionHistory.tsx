import type { TextVersion } from "@/lib/types";

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function VersionHistory({
  versions,
  currentVersion,
}: {
  versions: TextVersion[];
  currentVersion: number;
}): React.ReactElement {
  const sortedVersions = [...versions].sort((a, b) => b.version - a.version);

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-medium text-neutral-400">텍스트 버전 이력</h2>
      {sortedVersions.length === 0 ? (
        <p className="text-sm text-neutral-500">편집 이력이 없습니다.</p>
      ) : (
        <ol className="divide-y divide-neutral-800 rounded-lg border border-neutral-800 bg-[#141414] px-5">
          {sortedVersions.map((item) => (
            <li key={item.version} className="flex items-center justify-between gap-4 py-4">
              <div className="flex items-center gap-2">
                <span className="text-sm text-neutral-300">v{item.version}</span>
                {item.version === currentVersion ? (
                  <span className="rounded bg-[#0ea5e9]/10 px-2 py-0.5 text-xs text-[#0ea5e9]">
                    현재
                  </span>
                ) : null}
              </div>
              <time className="text-xs text-neutral-500" dateTime={item.created_at}>
                {formatDate(item.created_at)}
              </time>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
