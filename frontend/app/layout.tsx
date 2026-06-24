import type { Metadata } from "next";
import "./globals.css";

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
      <body>{children}</body>
    </html>
  );
}
