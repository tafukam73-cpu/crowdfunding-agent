"""projects テーブルに混入した日本の成功事例を削除する整理スクリプト。

Makuake / GreenFunding は「海外営業対象」ではなく日本の成功事例（比較用）であり、
本来 japanese_success_projects にのみ保存される。過去に収集パイプラインの配線
（旧 SUPPORTED_SITES）経由で projects に取り込まれてしまった分をここで削除する。

projects への外部キー（evaluations / email_drafts / availability_checks）は
ondelete=CASCADE、crm activities は SET NULL のため、projects の DELETE で子レコードは
DB 側のカスケードで整理される。

実行例（コンテナ内）:
    # 件数だけ確認（削除しない）
    docker compose exec backend python -m scripts.cleanup_japanese_projects_from_projects --dry-run

    # 実際に削除
    docker compose exec backend python -m scripts.cleanup_japanese_projects_from_projects
"""
from __future__ import annotations

import argparse

from sqlalchemy import delete, func, select

from app.db.session import SessionLocal
from app.models.project import JAPANESE_SUCCESS_SITES, Project

# 削除対象（営業対象外）サイトの値
TARGET_SITE_VALUES = [s.value for s in JAPANESE_SUCCESS_SITES]


def count_targets(db) -> dict[str, int]:
    """サイト別の削除対象件数を返す。"""
    rows = db.execute(
        select(Project.source_site, func.count())
        .where(Project.source_site.in_(TARGET_SITE_VALUES))
        .group_by(Project.source_site)
    ).all()
    return {site: cnt for site, cnt in rows}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="projects から Makuake / GreenFunding の案件を削除する"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="削除せず、対象件数だけ表示する",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        counts = count_targets(db)
        total = sum(counts.values())

        print("=== projects 内の日本成功事例（営業対象外）===")
        for site in TARGET_SITE_VALUES:
            print(f"  {site:14s}: {counts.get(site, 0)} 件")
        print(f"  {'合計':14s}: {total} 件")

        if total == 0:
            print("削除対象はありません。")
            return 0

        if args.dry_run:
            print("--dry-run のため削除は行いませんでした。")
            return 0

        result = db.execute(
            delete(Project).where(Project.source_site.in_(TARGET_SITE_VALUES))
        )
        db.commit()
        print(f"削除しました: {result.rowcount} 件（子レコードは DB カスケードで削除）")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
