"""Zeczec 案件の詳細補完バッチ（重い処理・バックグラウンド/CLI 用）。

一覧からしか取れていない Zeczec 案件について、詳細ページ（Playwright）から
メーカー名・カテゴリ・説明・公式サイト候補を非破壊で補完する。

使い方（backend ディレクトリ / コンテナ内）:
    python scripts/enrich_zeczec.py                 # 未補完の Zeczec 全件
    python scripts/enrich_zeczec.py --limit 10      # 先頭 10 件
    python scripts/enrich_zeczec.py --all           # 補完済み含め再取得
    python scripts/enrich_zeczec.py --ids 104 105   # ID 指定
    python scripts/enrich_zeczec.py --no-search     # 検索補完を無効化

同期処理で画面を塞がないよう、これは API リクエスト外で実行する CLI/ジョブ。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# backend ディレクトリを import パスに追加（`python scripts/enrich_zeczec.py` 実行時）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description="Zeczec 詳細補完バッチ")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--ids", type=int, nargs="*", default=None)
    ap.add_argument("--all", action="store_true", help="補完済みも再取得")
    ap.add_argument("--no-search", action="store_true", help="検索補完を無効化")
    args = ap.parse_args()

    from app.services import zeczec_enrichment_service as zes

    search_fn = None
    if not args.no_search:
        from app.services.search_providers import get_search_fn

        search_fn = get_search_fn()

    def progress(msg, pct=None):
        pct_s = f"{int((pct or 0) * 100):3d}%" if pct is not None else "   "
        print(f"  {pct_s} {msg}", flush=True)

    try:
        summary = zes.run_enrichment_batch(
            project_ids=args.ids,
            only_missing=not args.all,
            limit=args.limit,
            search_fn=search_fn,
            progress_cb=progress,
        )
    finally:
        if search_fn is not None:
            try:
                search_fn.close()
            except Exception:  # noqa: BLE001
                pass

    print("\n===== 補完結果 =====")
    print(f"対象 {summary['total']} 件 / 補完 {summary['enriched']} 件 / "
          f"ブロック {summary['blocked']} 件")
    for r in summary["results"]:
        if r.get("skipped") or r.get("error"):
            print(f"  - id={r['project_id']}: {r.get('skipped') or r.get('error')}")
            continue
        print(f"  - id={r['project_id']} maker={r.get('maker_name')!r} "
              f"cat={r.get('category')!r} url={r.get('maker_url')!r} "
              f"desc={'有' if r.get('has_description') else '無'} "
              f"updated={r.get('updated_fields')} challenged={r.get('challenged')}")
    print(json.dumps(summary, ensure_ascii=False, default=str)[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
