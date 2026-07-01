"""Brave 検索の直接デバッグ CLI（本番と同じ検索処理を使う）。

Web Research / Search Agent と同一の関数（search_providers.brave_search_once /
SearchRunner）を使って、実際に送る URL・status・body 先頭・web.results 件数・抽出
URL を出力する。Brave が 0 件/失敗の理由（401/403/429/キー未設定/parse error/空/
例外）も表示し、必要なら DuckDuckGo フォールバックの結果も見せる。

実行（backend ディレクトリで）:
    python -m scripts.debug_brave_search "RiseFit AI"
    python -m scripts.debug_brave_search "risefit official website"
    python -m scripts.debug_brave_search "Vitesy Facebook"
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.config import settings  # noqa: E402
from app.services import search_providers as sp  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python -m scripts.debug_brave_search "<query>" ["<query2>" ...]')
        return 2
    queries = sys.argv[1:]

    print("=== settings ===")
    print(f"SEARCH_PROVIDER = {settings.search_provider!r}")
    print(f"resolve_provider() = {sp.resolve_provider()!r}")
    print(f"BRAVE_SEARCH_API_KEY set = {bool(settings.brave_search_api_key)}")
    print(f"search_max_results = {settings.search_max_results}")

    count = settings.search_max_results
    client = sp._new_client()
    try:
        for q in queries:
            print("\n" + "=" * 70)
            print(f"QUERY: {q!r}")
            # 1) Brave を直接（本番と同じ brave_search_once）
            diag = sp.brave_search_once(client, q, count)
            print(f"  sent url    : {diag['url']}")
            print(f"  status      : {diag['status']}")
            print(f"  reason      : {diag['reason']}")
            print(f"  web.results : {len(diag['results'])}")
            print(f"  body[:1000] : {diag['body_head'][:1000]!r}")
            for r in diag["results"][:10]:
                print(f"    - {r.get('url')}")
    finally:
        client.close()

    # 2) 本番の統合ランナー（Brave 0件→DuckDuckGo フォールバック）でも確認
    print("\n" + "=" * 70)
    print("=== SearchRunner (primary -> DuckDuckGo fallback) ===")
    runner = sp.get_search_fn(count)
    try:
        for q in queries:
            results = runner(q)
            d = runner.diagnostics[-1]
            print(f"\nQUERY: {q!r}")
            print(f"  provider={d['provider']} status={d['status']} "
                  f"fallback={d['fallback']} results={d['results']}")
            print(f"  reason: {d['reason']}")
            for u in d["urls"]:
                print(f"    - {u}")
    finally:
        runner.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
