"""モック評価器。

外部 API を使わず、案件の属性からヒューリスティックに評価を生成する。
同一案件には毎回同じ結果（決定的）。Claude 実装が入るまでの
画面・DB・API 構造の検証用。
"""
from __future__ import annotations

import hashlib

from app.ai.evaluator import AXES, EvaluationResult, Evaluator, score_to_recommendation
from app.ai.ulule import product_assessment as ulule_product
from app.ai.ulule import signals as ulule_signals
from app.models.project import Project


def _clamp(v: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(v)))

# ガジェット性が高いと判断するカテゴリのキーワード
_GADGET_KEYWORDS = (
    "tech", "gadget", "hardware", "electronics", "design",
    "ガジェット", "テクノロジー", "audio", "robot", "iot",
)


def _seed(project: Project) -> int:
    """案件ごとに決定的な擬似乱数シードを作る。"""
    key = (project.source_url or project.title or str(project.id) or "x").encode("utf-8")
    return int(hashlib.sha256(key).hexdigest(), 16)


def _jitter(seed: int, salt: int, lo: int, hi: int) -> int:
    """seed と salt から [lo, hi] の決定的な値を得る。"""
    span = hi - lo + 1
    return lo + ((seed >> (salt % 64)) + salt * 2654435761) % span


class MockEvaluator(Evaluator):
    name = "mock-v1"

    def evaluate(self, project: Project) -> EvaluationResult:
        seed = _seed(project)

        category = (project.category or "").lower()
        is_gadget = any(k in category for k in _GADGET_KEYWORDS)
        has_video = bool(project.video_url)
        backers = project.backers_count or 0

        # --- 軸別スコア（0〜100、決定的） ---
        axis: dict[str, int] = {}
        axis["日本未上陸の可能性"] = _jitter(seed, 1, 55, 95)
        axis["ガジェット性"] = _jitter(seed, 2, 70, 95) if is_gadget else _jitter(seed, 2, 25, 60)
        axis["動画映え"] = _jitter(seed, 3, 65, 95) if has_video else _jitter(seed, 3, 30, 65)
        axis["新規性"] = _jitter(seed, 4, 40, 90)
        # 支援者が多いほど日本クラファン適性を高めに
        momentum = min(backers, 3000) / 3000  # 0..1
        axis["日本クラファン適性"] = int(_jitter(seed, 5, 45, 80) * (0.8 + 0.2 * momentum))
        axis["Makuake向き"] = _jitter(seed, 6, 45, 90)
        axis["GreenFunding向き"] = _jitter(seed, 7, 40, 85)
        axis["営業すべきか"] = _jitter(seed, 8, 40, 95)

        # --- Ulule 案件は優先商材（サステナブル/デザイン/ライフスタイル）を加点 ---
        # 6 軸の追加スコア・理由文は evaluation_service で両エンジン共通に付与する。
        sig = ulule_signals(project)
        if sig["is_ulule"]:
            hi = len(sig["high_hits"])
            bonus = min(20, 6 * hi)  # 高評価キーワードに応じて加点
            if sig["sustainability"]:
                axis["新規性"] = _clamp(axis["新規性"] + 8)
            if sig["europe_design"]:
                axis["動画映え"] = _clamp(axis["動画映え"] + 6)  # デザイン映え
            if sig["lifestyle_fit"]:
                axis["日本クラファン適性"] = _clamp(axis["日本クラファン適性"] + bonus)
                axis["Makuake向き"] = _clamp(axis["Makuake向き"] + bonus)
                axis["GreenFunding向き"] = _clamp(axis["GreenFunding向き"] + bonus)
                axis["営業すべきか"] = _clamp(axis["営業すべきか"] + bonus)
            if sig["low_hits"]:
                # 映画/音楽/寄付など営業対象外寄りは減点
                axis["営業すべきか"] = _clamp(axis["営業すべきか"] - 15)
                axis["日本クラファン適性"] = _clamp(axis["日本クラファン適性"] - 10)
            # 寄付/観光/文化/団体支援などの非商品（営業対象外）は大きく減点する
            if not ulule_product(project)["is_sales_target_candidate"]:
                axis["営業すべきか"] = _clamp(axis["営業すべきか"] - 40)
                axis["日本クラファン適性"] = _clamp(axis["日本クラファン適性"] - 30)
                axis["Makuake向き"] = _clamp(axis["Makuake向き"] - 25)
                axis["GreenFunding向き"] = _clamp(axis["GreenFunding向き"] - 25)

        # AXES の順序を保証
        axis_scores = {k: int(axis[k]) for k in AXES}

        total = round(sum(axis_scores.values()) / len(axis_scores))
        recommendation = score_to_recommendation(total)

        # --- テキスト（テンプレート） ---
        reasons = "・" + "\n・".join(
            [
                f"日本未上陸の可能性が{'高い' if axis_scores['日本未上陸の可能性'] >= 70 else '一定程度ある'}",
                f"{'ガジェット系で動画映えしやすい' if is_gadget else 'カテゴリは要検討だが訴求は可能'}",
                f"支援者数 {backers:,} 人で関心度は{'高い' if backers >= 500 else '標準的'}",
            ]
        )
        concerns = "・" + "\n・".join(
            [
                "日本国内での競合・既出の有無を要確認",
                "技適/PSE 等の認証や輸入規制の確認が必要な場合あり",
                "メーカーの独占販売権交渉の可否は未確認",
            ]
        )
        rec_label = {"high": "高", "mid": "中", "low": "低"}[recommendation.value]
        sales_comment = (
            f"推奨度は『{rec_label}』。"
            + (
                "Makuake/GreenFunding 向きで早期アプローチを推奨。"
                if recommendation.value == "high"
                else "条件を精査のうえ優先度を判断。"
            )
            + "（※モック評価。Claude 連携で精度向上予定）"
        )

        return EvaluationResult(
            total_score=total,
            recommendation=recommendation,
            axis_scores=axis_scores,
            reasons=reasons,
            concerns=concerns,
            sales_comment=sales_comment,
            model=self.name,
        )
