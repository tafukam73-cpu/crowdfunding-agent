"""全案件の description_clean を description から再生成する CLI。

過去に保存された project.description が生 HTML のままで description_clean が
未生成（null）／古い案件について、HTML 除去済みの読みやすい概要を作り直して
保存する。対象は Kickstarter / Indiegogo / Ulule / Wadiz / その他すべて。

処理内容（app.ai.ulule.clean_description / app.util.text.html_to_text）:
  - figure / img / script / style / svg / video / iframe を内容ごと除去
  - 画像 URL・alt 文字列・キャプションの混入を防止
  - HTML エンティティ（&amp; 等）をデコード
  - 余分な改行・空白を整理し、500〜1000 文字程度にトリム

実行例（コンテナ内）:
    # 変更せず、更新対象の件数だけ表示
    docker compose exec backend python -m scripts.rebuild_project_descriptions --dry-run

    # 全件再生成して保存
    docker compose exec backend python -m scripts.rebuild_project_descriptions
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.ai.ulule import clean_description
from app.db.session import SessionLocal
from app.models.project import Project


def main() -> int:
    parser = argparse.ArgumentParser(
        description="全案件の description_clean を description から再生成する"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="保存せず、更新される件数だけ表示する",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        total = 0
        changed = 0
        filled = 0  # null → 値ありになった件数
        for project in db.scalars(select(Project)):
            total += 1
            new_clean = clean_description(project.description)
            if new_clean != project.description_clean:
                changed += 1
                if not project.description_clean:
                    filled += 1
                if not args.dry_run:
                    project.description_clean = new_clean

        if args.dry_run:
            print(
                f"[dry-run] 全 {total} 件中 {changed} 件が更新対象"
                f"（うち未生成→生成 {filled} 件）。保存はしていません。"
            )
            return 0

        db.commit()
        print(
            f"description_clean を再生成しました：全 {total} 件中 {changed} 件更新"
            f"（うち未生成→生成 {filled} 件）。"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
