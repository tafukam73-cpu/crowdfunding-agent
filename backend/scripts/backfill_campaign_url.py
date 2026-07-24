"""campaign_url（海外クラファン商品ページ URL）のバックフィル。

campaign_url の正規フィールドは ``projects.source_url``。source_site と整合しない
（＝案件ページではない / 空）行について、**既存データから安全に復元できるものだけ**
を更新する。推測 URL は絶対に保存しない。

復元元（この順に探す。いずれも実データであり生成しない）:
  1. discovered_products.source_url … 昇格元の発掘レコード（promoted_project_id で紐づく）
  2. projects.enrichment 内の URL   … 詳細補完で記録した取得元 URL

採用条件: URL のホストが projects.source_site のクラファンドメイン配下であること
（app.services.campaign_url.host_matches）。1 件も条件を満たさなければ更新しない。

実行（backend ディレクトリで）:
    python scripts/backfill_campaign_url.py            # dry-run（更新しない）
    python scripts/backfill_campaign_url.py --apply    # 実際に更新する
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.discovered_product import DiscoveredProduct  # noqa: E402
from app.models.project import SALES_TARGET_SITES, Project  # noqa: E402
from app.services import campaign_url as cu  # noqa: E402

_SALES_TARGET_VALUES = [s.value for s in SALES_TARGET_SITES]


def _enrichment_urls(project: Project) -> list[str]:
    """enrichment に記録された URL 文字列を列挙する（形は問わず走査する）。"""
    out: list[str] = []

    def walk(node) -> None:
        if isinstance(node, str):
            if node.startswith("http"):
                out.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(project.enrichment or {})
    return out


def find_candidate(db, project: Project) -> tuple[str, str] | None:
    """(url, 復元元) を返す。安全に復元できなければ None。"""
    promoted = db.scalar(
        select(DiscoveredProduct).where(
            DiscoveredProduct.promoted_project_id == project.id
        )
    )
    if promoted is not None and cu.host_matches(promoted.source_url, project.source_site):
        return str(promoted.source_url).strip(), "discovered_products.source_url"

    for url in _enrichment_urls(project):
        if cu.host_matches(url, project.source_site):
            return url.strip(), "projects.enrichment"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="実際に更新する")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        projects = list(
            db.scalars(
                select(Project).where(Project.source_site.in_(_SALES_TARGET_VALUES))
            )
        )
        total = len(projects)
        ok = [p for p in projects if cu.campaign_url_of(p) is not None]
        missing = [p for p in projects if cu.campaign_url_of(p) is None]

        restored: list[tuple[int, str, str]] = []
        unresolved: list[tuple[int, str, str | None]] = []
        for p in missing:
            found = find_candidate(db, p)
            if found is None:
                unresolved.append((p.id, p.source_site, cu.missing_reason(p)))
                continue
            url, origin = found
            restored.append((p.id, url, origin))
            if args.apply:
                p.source_url = url

        if args.apply and restored:
            db.commit()

        print(f"対象（営業対象サイト）: {total} 件")
        print(f"  campaign_url あり     : {len(ok)} 件")
        print(f"  campaign_url なし     : {len(missing)} 件")
        print(f"  → 復元できた           : {len(restored)} 件"
              + ("（更新済み）" if args.apply else "（dry-run・未更新）"))
        print(f"  → 復元できず残る       : {len(unresolved)} 件")
        for pid, url, origin in restored[:20]:
            print(f"    restore project={pid} <- {origin}: {url}")
        for pid, site, reason in unresolved[:20]:
            print(f"    unresolved project={pid} site={site} reason={reason}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
