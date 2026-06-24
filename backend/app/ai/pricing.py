"""Claude モデルの料金表とコスト計算。

単価は USD / 100万トークン (input, output)。SDK のトークン数からコストを算出する。
"""
from __future__ import annotations

PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}

# 1 案件あたりの評価トークン概算（実測の余裕を見た値）
EVAL_EST_INPUT_TOKENS = 700
EVAL_EST_OUTPUT_TOKENS = 300


def is_priced(model: str) -> bool:
    return model in PRICING


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """トークン数から推定コスト(USD)を計算。料金表に無いモデルは 0。"""
    rate = PRICING.get(model)
    if not rate:
        return 0.0
    return input_tokens / 1_000_000 * rate[0] + output_tokens / 1_000_000 * rate[1]
