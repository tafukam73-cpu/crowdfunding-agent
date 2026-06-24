"""AI 実装スモークテスト（最小トークン）。

実案件 1 件だけに対して AI 評価とメール下書き生成を実行し、
レスポンス形式・DB 保存・使用モデル・トークン使用量・推定コストを確認する。

実行例（コンテナ内）:
    docker compose exec backend python -m scripts.smoke_test
    docker compose exec backend python -m scripts.smoke_test 3   # 案件ID指定

ANTHROPIC_API_KEY 設定済みなら Claude、未設定ならモックで動く。
API 失敗時はモックへフォールバックせず、エラーを明示表示する（要件どおり）。
"""
from __future__ import annotations

import sys

from sqlalchemy import func, select

from app.ai import get_email_generator, get_evaluator
from app.config import settings
from app.db.session import SessionLocal
from app.models.email_draft import EmailDraft
from app.models.evaluation import AiEvaluation
from app.models.project import Project
from app.services import email_service, evaluation_service

# モデル別 料金（USD / 100万トークン）: (input, output)
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}


def _report_usage(label: str, model: str, usage: dict | None) -> None:
    if not usage:
        print(f"  {label}: usage 情報なし（モック経路）")
        return
    in_t = usage.get("input_tokens", 0)
    out_t = usage.get("output_tokens", 0)
    rate = PRICING.get(model)
    if rate:
        cost = in_t / 1_000_000 * rate[0] + out_t / 1_000_000 * rate[1]
        cost_str = f"${cost:.6f}"
    else:
        cost_str = "(料金表に未登録)"
    print(f"  {label}: input={in_t} output={out_t} 推定コスト={cost_str}")


def main() -> int:
    mode = "claude" if settings.anthropic_api_key else "mock"
    print(f"=== AI スモークテスト (mode={mode}, model={settings.anthropic_model}) ===")

    db = SessionLocal()
    try:
        if len(sys.argv) > 1:
            project = db.get(Project, int(sys.argv[1]))
        else:
            project = db.scalar(select(Project).order_by(Project.id).limit(1))

        if project is None:
            print("案件がありません。先に POST /scrape/run で収集してください。")
            return 1

        print(f"対象案件: id={project.id} | {project.title[:40]!r} ({project.source_site})")

        # 評価器/生成器を直接生成し、サービスへ注入（usage を取得するため）
        evaluator = get_evaluator()
        generator = get_email_generator()

        # --- AI 評価（1 件） ---
        print("\n[1] AI 評価を実行...")
        try:
            ev = evaluation_service.evaluate_project(db, project, evaluator=evaluator)
            print(f"  OK model={ev.model} total_score={ev.total_score} "
                  f"recommendation={ev.recommendation} axes={len(ev.axis_scores)}")
            _report_usage("usage", ev.model, getattr(evaluator, "last_usage", None))
            ev_count = db.scalar(
                select(func.count()).select_from(AiEvaluation)
                .where(AiEvaluation.project_id == project.id)
            )
            print(f"  DB: ai_evaluations 件数(この案件) = {ev_count} / "
                  f"latest_score={project.latest_score}")
        except Exception as exc:  # noqa: BLE001  失敗を明示（モックへフォールバックしない）
            db.rollback()
            print(f"  NG 評価失敗（エラー表示のみ・モックへ切替なし）: "
                  f"{type(exc).__name__}: {exc}")

        # --- 営業メール下書き（1 案件分・3 種別） ---
        print("\n[2] 営業メール下書きを生成...")
        try:
            drafts = email_service.generate_drafts(db, project, generator=generator)
            for d in drafts:
                print(f"  - [{d.email_type}] model={d.model} subject={d.subject[:50]!r}")
            _report_usage("usage(3通合計)", drafts[0].model if drafts else "",
                          getattr(generator, "last_usage", None))
            d_count = db.scalar(
                select(func.count()).select_from(EmailDraft)
                .where(EmailDraft.project_id == project.id)
            )
            print(f"  DB: email_drafts 件数(この案件) = {d_count}")
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            print(f"  NG メール生成失敗（エラー表示のみ・モックへ切替なし）: "
                  f"{type(exc).__name__}: {exc}")

        print("\n=== 完了 ===")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
