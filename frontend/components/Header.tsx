import Link from "next/link";

export default function Header() {
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <div className="flex items-center gap-6">
          <Link href="/" className="text-lg font-bold text-slate-900">
            クラファン案件発掘ダッシュボード
          </Link>
          <nav className="flex items-center gap-4 text-sm">
            <Link href="/" className="text-slate-600 hover:text-slate-900">
              海外案件
            </Link>
            <Link
              href="/japanese-success"
              className="text-slate-600 hover:text-slate-900"
            >
              日本の成功事例
            </Link>
          </nav>
        </div>
        <span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-500">
          Step 5
        </span>
      </div>
    </header>
  );
}
