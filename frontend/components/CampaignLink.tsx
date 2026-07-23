"use client";

/**
 * 海外クラファンの「商品ページを開く」リンク。
 *
 * campaign_url はメーカー公式サイト（official_site_url）とは別物で、公式サイトで
 * 代用しない。取得できていない場合は URL を出さず「商品ページURL未確認」を表示する
 * （どこを見ても同じ表現・同じ挙動になるよう、この 1 コンポーネントに集約する）。
 */

export type CampaignLinkSource = {
  campaign_url?: string | null;
  campaign_url_missing?: boolean | null;
  campaign_url_missing_reason?: string | null;
};

const MISSING_REASON_LABELS: Record<string, string> = {
  no_source_url: "商品ページURLが未取得",
  invalid_url: "商品ページURLが無効（ダミー/形式不正）",
  not_campaign_domain: "取得元サイトのドメインと一致しない",
  unsupported_site: "対応外の取得元サイト",
};

export function campaignUrlOf(src: CampaignLinkSource | null | undefined): string | null {
  if (!src) return null;
  if (src.campaign_url_missing) return null;
  return src.campaign_url ?? null;
}

export default function CampaignLink({
  source,
  size = "sm",
  className = "",
}: {
  source: CampaignLinkSource | null | undefined;
  size?: "sm" | "md";
  className?: string;
}) {
  const url = campaignUrlOf(source);
  const pad = size === "md" ? "px-3 py-1.5 text-sm" : "px-2 py-1 text-xs";

  if (!url) {
    const reason = source?.campaign_url_missing_reason ?? null;
    return (
      <span
        className={`inline-flex items-center gap-1 rounded border border-amber-300 bg-amber-50 font-medium text-amber-800 ${pad} ${className}`}
        title={reason ? MISSING_REASON_LABELS[reason] ?? reason : undefined}
      >
        商品ページURL未確認
      </span>
    );
  }

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      title={url}
      className={`inline-flex items-center gap-1 rounded border border-blue-300 bg-blue-50 font-medium text-blue-700 hover:bg-blue-100 ${pad} ${className}`}
    >
      商品ページを開く ↗
    </a>
  );
}
