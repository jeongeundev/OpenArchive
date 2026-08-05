import Link from "next/link";

import { UserSwitcher } from "./UserSwitcher";

export function SiteHeader(): React.ReactElement {
  return (
    <header className="border-b border-neutral-800 bg-[#0a0a0a]">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-6 py-4">
        <div className="flex items-center gap-6">
          <Link className="font-semibold text-white" href="/">
            OpenArchive
          </Link>
          <nav aria-label="주요 메뉴" className="flex items-center gap-4 text-sm">
            <Link className="text-neutral-400 hover:text-[#0ea5e9]" href="/">
              문서
            </Link>
            <Link className="text-neutral-400 hover:text-[#0ea5e9]" href="/search">
              검색
            </Link>
          </nav>
        </div>
        <UserSwitcher />
      </div>
    </header>
  );
}
