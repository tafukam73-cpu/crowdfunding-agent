"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// 左メニュー（グローバルナビ）。営業の 1 日の動線に絞った 5 項目のみを置く。
// 収集・コスト・CRM 等の管理系は「設定」配下から辿る。
const NAV: { href: string; label: string; icon: string; exact?: boolean }[] = [
  { href: "/", label: "ホーム", icon: "🏠", exact: true },
  { href: "/projects", label: "営業案件", icon: "📋" },
  { href: "/tasks", label: "今日のタスク", icon: "✅" },
  { href: "/sales-copilot-v2", label: "AI秘書", icon: "🤖" },
  { href: "/settings", label: "設定", icon: "⚙️" },
];

function isActive(pathname: string, href: string, exact?: boolean): boolean {
  if (exact) return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function Sidebar() {
  const pathname = usePathname() ?? "/";

  return (
    <aside className="w-full shrink-0 border-b border-slate-200 bg-white md:sticky md:top-0 md:h-screen md:w-56 md:border-b-0 md:border-r">
      <div className="flex items-center gap-2 px-4 py-3 md:py-4">
        <Link href="/" className="text-sm font-bold text-slate-900">
          営業AIホーム
        </Link>
      </div>
      <nav className="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col md:px-3 md:pb-4">
        {NAV.map((item) => {
          const active = isActive(pathname, item.href, item.exact);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={`flex shrink-0 items-center gap-2 rounded-md px-3 py-2 text-sm transition ${
                active
                  ? "bg-slate-900 font-medium text-white"
                  : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              }`}
            >
              <span aria-hidden>{item.icon}</span>
              <span className="whitespace-nowrap">{item.label}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
