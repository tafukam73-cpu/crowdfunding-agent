"""AI 評価。

get_evaluator() が設定に応じて評価器を選ぶ：
  - ANTHROPIC_API_KEY 未設定 → MockEvaluator（既定）
  - 設定済み            → ClaudeEvaluator（雛形。実装後に有効）
"""
from __future__ import annotations

from app.ai.email_generator import EmailDraftResult, EmailGenerator
from app.ai.evaluator import EvaluationResult, Evaluator
from app.ai.mock_email_generator import MockEmailGenerator
from app.ai.mock_evaluator import MockEvaluator
from app.config import settings


def get_evaluator() -> Evaluator:
    if settings.anthropic_api_key:
        # 遅延 import：anthropic 未導入でもモック経路は動く
        from app.ai.claude_evaluator import ClaudeEvaluator

        return ClaudeEvaluator(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    return MockEvaluator()


def get_email_generator() -> EmailGenerator:
    if settings.anthropic_api_key:
        from app.ai.claude_email_generator import ClaudeEmailGenerator

        return ClaudeEmailGenerator(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )
    return MockEmailGenerator()


__all__ = [
    "get_evaluator",
    "Evaluator",
    "EvaluationResult",
    "get_email_generator",
    "EmailGenerator",
    "EmailDraftResult",
]
