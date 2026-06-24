import Link from "next/link";

export default function Header() {
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
        <Link href="/" className="text-lg font-bold text-slate-900">
          クラファン案件発掘ダッシュボード
        </Link>
        <span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-500">
          Step 2
        </span>
      </div>
    </header>
  );
}
