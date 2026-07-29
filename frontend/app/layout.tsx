import type { Metadata } from "next";
import "./globals.css";

import Sidebar from "@/components/Sidebar";

export const metadata: Metadata = {
  title: "海外クラファン案件発掘・営業支援システム",
  description: "海外クラウドファンディング案件を収集し、日本向け商品を発掘する",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja">
      <body>
        {/* 左メニュー＋コンテンツの 2 カラム。狭い画面では上部の横並びナビになる。 */}
        <div className="flex min-h-screen flex-col bg-slate-50 md:flex-row">
          <Sidebar />
          <div className="min-w-0 flex-1">{children}</div>
        </div>
      </body>
    </html>
  );
}
