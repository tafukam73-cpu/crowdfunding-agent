import {
  SALES_STATUS_COLORS,
  SALES_STATUS_LABELS,
  normalizeSalesStatus,
  type SalesStatus,
} from "@/lib/api";

/**
 * 営業状況（sales_status）バッジ。案件単位パイプラインの単一正本を表示する。
 * won（非推奨）は contract_agreed へ正規化して表示する。
 */
export default function SalesStatusBadge({
  status,
}: {
  status: SalesStatus | string | null | undefined;
}) {
  const s = normalizeSalesStatus(status);
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${SALES_STATUS_COLORS[s]}`}
    >
      {SALES_STATUS_LABELS[s]}
    </span>
  );
}
