"""Claude 評価器。

ANTHROPIC_API_KEY が設定されると get_evaluator() がこれを使う。
公式 anthropic SDK の Messages API を、構造化出力（output_config.format）で呼び、
JSON で評価結果を受け取って EvaluationResult にマップする。

失敗（API エラー / JSON 不正 等）は呼び出し側（evaluation_service / router）で
捕捉・記録され、アプリ全体は落とさない。
"""
from __future__ import annotations

import json
import logging

from app.ai.evaluator import (
    AXES,
    EvaluationResult,
    Evaluator,
    score_to_recommendation,
)
from app.models.project import Project

logger = logging.getLogger("ai.claude_evaluator")

SYSTEM_PROMPT = (
    "あなたは日本のクラウドファンディング（Makuake / GreenFunding）に精通した"
    "目利きバイヤーです。海外クラファン案件を、日本市場での成功可能性と"
    "営業価値の観点から厳密に評価します。出力は指定された JSON スキーマに"
    "厳密に従ってください。"
)

# 構造化出力スキーマ（軸キーは日本語。数値範囲制約は JSON Schema では
# 表現できないため、受信後にクランプする）
EVAL_SCHEMA = {
    "type": "object",
    "properties": {
        "total_score": {"type": "integer"},
        "recommendation": {"type": "string", "enum": ["high", "mid", "low"]},
        "axis_scores": {
            "type": "object",
            "properties": {axis: {"type": "integer"} for axis in AXES},
            "required": list(AXES),
            "additionalProperties": False,
        },
        "reasons": {"type": "string"},
        "concerns": {"type": "string"},
        "sales_comment": {"type": "string"},
    },
    "required": [
        "total_score",
        "recommendation",
        "axis_scores",
        "reasons",
        "concerns",
        "sales_comment",
    ],
    "additionalProperties": False,
}


def _clamp(value, lo: int = 0, hi: int = 100) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return lo


class ClaudeEvaluator(Evaluator):
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-opus-4-8") -> None:
        # 遅延 import：anthropic 未導入環境でもモック経路は動く
        from anthropic import Anthropic

        self.model = model
        self.name = model  # DB には実モデル名を記録
        self._client = Anthropic(api_key=api_key)

    def _build_prompt(self, project: Project) -> str:
        axes = "、".join(AXES)
        base = (
            f"次の海外クラファン案件を評価してください。\n"
            f"各評価軸（{axes}）を 0〜100 でスコア化し、総合スコア total_score(0-100)、"
            f"推奨度 recommendation(high/mid/low)、評価理由 reasons、懸念点 concerns、"
            f"営業推奨コメント sales_comment を日本語で記述してください。\n\n"
            f"# 案件\n"
            f"タイトル: {project.title}\n"
            f"収集元: {project.source_site}\n"
            f"カテゴリ: {project.category}\n"
            f"説明: {project.description}\n"
            f"通貨/目標額/調達額: {project.currency} / {project.goal_amount} / {project.raised_amount}\n"
            f"支援者数: {project.backers_count}\n"
            f"動画: {'あり' if project.video_url else 'なし'}\n"
            f"メーカー: {project.maker_name}\n"
        )
        # Ulule 案件はサステナブル/デザイン/ライフスタイル観点を追加で評価させる
        from app.ai.ulule import is_ulule, prompt_block

        if is_ulule(project):
            base += prompt_block(project)
        return base

    def _parse(self, raw_json: str) -> EvaluationResult:
        data = json.loads(raw_json)
        axis_scores = {axis: _clamp(data.get("axis_scores", {}).get(axis, 0)) for axis in AXES}
        total = _clamp(
            data.get("total_score", round(sum(axis_scores.values()) / len(AXES)))
        )
        rec = data.get("recommendation") or score_to_recommendation(total).value
        return EvaluationResult(
            total_score=total,
            recommendation=rec,
            axis_scores=axis_scores,
            reasons=data.get("reasons", ""),
            concerns=data.get("concerns", ""),
            sales_comment=data.get("sales_comment", ""),
            model=self.name,
        )

    def evaluate(self, project: Project) -> EvaluationResult:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": self._build_prompt(project)}],
            output_config={"format": {"type": "json_schema", "schema": EVAL_SCHEMA}},
        )
        self.last_usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if not text:
            raise ValueError("Claude 応答に JSON テキストが含まれていません")
        return self._parse(text)
