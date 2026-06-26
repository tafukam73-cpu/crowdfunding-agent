"""スクレイピング用 User-Agent プール。

403/ボット対策として、リトライ時に UA をローテーションする。実在のブラウザに
近い新しめの UA を複数用意する。
"""
from __future__ import annotations

USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) "
    "Gecko/20100101 Firefox/132.0",
]


def ua_for_attempt(attempt: int) -> str:
    """試行回数に応じて UA を選ぶ（リトライごとに変える）。"""
    return USER_AGENTS[attempt % len(USER_AGENTS)]
