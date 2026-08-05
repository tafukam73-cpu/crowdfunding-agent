import {
  QUALIFICATION_COLORS,
  QUALIFICATION_LABELS,
  QUALIFICATION_UNKNOWN_COLOR,
  QUALIFICATION_UNKNOWN_LABEL,
  normalizeQualification,
  type QualificationDecision,
} from "@/lib/api";

/**
 * 営業対象判定バッジ。
 *
 * 一覧で使う値は **pre_research スナップショット専用**なので、clear を
 * 「営業可能」「送信可能」とは表示しない（送信可否は pre_outreach で別に判定され、
 * 結果が変わり得るため）。色だけに依存させず、必ず文字を併記する。
 */
export default function QualificationBadge({
  decision,
  title,
}: {
  decision: QualificationDecision | string | null | undefined;
  /** 補足説明（例: 判定日時）。既定でも意味が伝わる文言を入れる。 */
  title?: string;
}) {
  const d = normalizeQualification(decision);
  const label = d ? QUALIFICATION_LABELS[d] : QUALIFICATION_UNKNOWN_LABEL;
  const color = d ? QUALIFICATION_COLORS[d] : QUALIFICATION_UNKNOWN_COLOR;
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${color}`}
      title={title ?? `営業対象判定（調査前）: ${label}`}
    >
      {label}
    </span>
  );
}
