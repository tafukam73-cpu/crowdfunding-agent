// Sales Copilot v2 の表示ロジック（純粋関数・ラベル・フィルター・並び替え）。
// UI から分離してテスト/型検査しやすくする。バックエンドの grade と同じ基準。
import type {
  AssessmentState,
  JapanCheckStatus,
  ScoreGrade,
  SalesCopilotV2Card,
  V2Decision,
} from "@/lib/api";

// スコア(0-100) → grade。バックエンド sales_assessment_service.grade と同一基準。
export function scoreGrade(score: number | null | undefined): ScoreGrade | null {
  if (score == null) return null;
  if (score >= 80) return "A";
  if (score >= 65) return "B";
  if (score >= 50) return "C";
  if (score >= 35) return "D";
  return "E";
}

export const GRADE_COLORS: Record<ScoreGrade, string> = {
  A: "bg-emerald-100 text-emerald-700",
  B: "bg-lime-100 text-lime-700",
  C: "bg-amber-100 text-amber-700",
  D: "bg-orange-100 text-orange-700",
  E: "bg-rose-100 text-rose-700",
};

export const DECISION_LABELS: Record<V2Decision, string> = {
  sell_now_exclusive: "独占営業（今すぐ）",
  sell_now: "今すぐ営業",
  needs_contact: "連絡先探索",
  needs_email: "メール生成",
  needs_research: "企業リサーチ",
  needs_followup: "フォロー",
  needs_negotiation: "商談対応",
  waiting: "返信待ち",
  deprioritize: "優先度低",
  drop: "見送り",
  closed: "対応済み",
  data_insufficient: "データ不足",
};

export const DECISION_COLORS: Record<string, string> = {
  sell_now_exclusive: "bg-rose-100 text-rose-700",
  sell_now: "bg-rose-100 text-rose-700",
  needs_contact: "bg-sky-100 text-sky-700",
  needs_email: "bg-indigo-100 text-indigo-700",
  needs_research: "bg-violet-100 text-violet-700",
  needs_followup: "bg-amber-100 text-amber-700",
  needs_negotiation: "bg-amber-100 text-amber-700",
  waiting: "bg-slate-100 text-slate-500",
  deprioritize: "bg-slate-100 text-slate-500",
  drop: "bg-slate-100 text-slate-400",
  closed: "bg-slate-100 text-slate-400",
  data_insufficient: "bg-slate-100 text-slate-500",
};

export const STATE_LABELS: Record<AssessmentState, string> = {
  evaluated: "評価済み",
  provisional: "暫定評価",
  checking_japan: "日本販売チェック中",
  recompute_pending: "再評価待ち",
  data_insufficient: "データ不足",
  failed: "評価失敗",
};

export const STATE_COLORS: Record<AssessmentState, string> = {
  evaluated: "bg-emerald-100 text-emerald-700",
  provisional: "bg-amber-100 text-amber-700",
  checking_japan: "bg-sky-100 text-sky-700",
  recompute_pending: "bg-indigo-100 text-indigo-700",
  data_insufficient: "bg-slate-100 text-slate-500",
  failed: "bg-rose-100 text-rose-700",
};

export const JAPAN_STATUS_LABELS: Record<JapanCheckStatus, string> = {
  not_checked: "未チェック",
  queued: "待機中",
  running: "チェック中",
  completed: "完了",
  failed: "失敗",
};

export const JAPAN_RESULT_LABELS: Record<string, string> = {
  sold_in_japan: "日本で販売あり",
  not_found_in_japan: "日本で未確認（未上陸の可能性）",
  inconclusive: "判定不能",
};

// フィルターキー。バケット判断 + タグ + 状態を横断して選べる。
export type V2FilterKey =
  | "all"
  | "sell_now_exclusive"
  | "needs_email"
  | "needs_contact"
  | "data_insufficient"
  | "deprioritize"
  | "high_japan_fit"
  | "high_exclusivity"
  | "makuake_promising"
  | "japan_not_checked"
  | "low_confidence";

export const FILTER_LABELS: Record<V2FilterKey, string> = {
  all: "すべて",
  sell_now_exclusive: "独占営業",
  needs_email: "メール生成",
  needs_contact: "連絡先探索",
  data_insufficient: "データ不足",
  deprioritize: "優先度低",
  high_japan_fit: "日本市場適性が高い",
  high_exclusivity: "独占可能性が高い",
  makuake_promising: "Makuake有望",
  japan_not_checked: "日本チェック未実施",
  low_confidence: "confidence低",
};

export function matchesFilter(card: SalesCopilotV2Card, key: V2FilterKey): boolean {
  const a = card.assessment;
  switch (key) {
    case "all":
      return true;
    case "sell_now_exclusive":
      return card.decision === "sell_now_exclusive" || card.decision === "sell_now";
    case "needs_email":
    case "needs_contact":
    case "data_insufficient":
    case "deprioritize":
      return card.decision === key;
    case "high_japan_fit":
      return (a.japan_market_fit.score ?? 0) >= 65;
    case "high_exclusivity":
      return (a.exclusivity.score ?? 0) >= 65;
    case "makuake_promising":
      return (a.makuake_fit.score ?? 0) >= 65;
    case "japan_not_checked":
      return (
        card.japan_sales_check.status === "not_checked" ||
        card.japan_sales_check.status === "failed"
      );
    case "low_confidence":
      return (a.confidence ?? 0) < 40;
    default:
      return true;
  }
}

export type V2SortKey =
  | "priority"
  | "japan_market_fit"
  | "exclusivity"
  | "makuake_fit"
  | "raised"
  | "backers"
  | "updated";

export const SORT_LABELS: Record<V2SortKey, string> = {
  priority: "総合優先度",
  japan_market_fit: "日本市場適性",
  exclusivity: "独占販売可能性",
  makuake_fit: "Makuake適性",
  raised: "調達額",
  backers: "支援者数",
  updated: "更新日時",
};

export function sortCards(
  cards: SalesCopilotV2Card[],
  key: V2SortKey
): SalesCopilotV2Card[] {
  const val = (c: SalesCopilotV2Card): number => {
    switch (key) {
      case "priority":
        return c.priority_score ?? 0;
      case "japan_market_fit":
        return c.assessment.japan_market_fit.score ?? 0;
      case "exclusivity":
        return c.assessment.exclusivity.score ?? 0;
      case "makuake_fit":
        return c.assessment.makuake_fit.score ?? 0;
      case "raised":
        return c.funding?.raised_amount ?? 0;
      case "backers":
        return c.funding?.backers_count ?? 0;
      case "updated":
        return c.updated_at ? Date.parse(c.updated_at) : 0;
      default:
        return 0;
    }
  };
  return [...cards].sort((a, b) => val(b) - val(a));
}

export const MISSING_DATA_LABELS: Record<string, string> = {
  funding_traction: "クラファン実績",
  japan_sales_check: "日本販売状況",
  contact: "連絡先",
  description: "商品説明",
};
