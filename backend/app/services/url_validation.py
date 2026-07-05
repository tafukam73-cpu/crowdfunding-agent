"""URL の共通バリデーション（参照元URL・公式サイトのダミー除外）。

AI 企業リサーチ等で `https://example.com/...` や `https://www.kickstarter.com/
projects/example/...` のようなダミー/プレースホルダー URL が「参照元 URL」として
表示される問題を防ぐための単一の入口。バックエンドの保存時・API 境界・
（フロントの isValidBusinessUrl と対で）確実に弾く。

主な公開 API:
  - is_valid_business_url(url)  -> bool
  - business_url_reason(url)    -> str | None  （無効なら機械可読な理由）
  - filter_business_urls(urls)  -> list[str]    （有効な URL のみ・重複排除）
  - verify_url_ok(url)          -> bool         （2xx/3xx なら True。404 等は False）
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

logger = logging.getLogger("url_validation")

# ダミー / プレースホルダーのホスト「ラベル」（ドット区切りのいずれかが一致で無効）。
# 例: example.com / example.org / xxxx.example.com / test.io / dummy.net / sample.com。
# 注意: example-brand.com のような正規ドメインは「example-brand」ラベルなので弾かない。
DUMMY_HOST_LABELS = frozenset(
    {
        "example",
        "dummy",
        "sample",
        "samples",
        "test",
        "tests",
        "testing",
        "placeholder",
        "localhost",
        "invalid",
        "yourdomain",
        "mydomain",
        "yourcompany",
        "mycompany",
        "domain",
        "acme",
        "foo",
        "bar",
        "baz",
        "githubusercontent",
    }
)

# ホスト名にこの文字列が含まれたら無効（raw.githubusercontent.com など）。
DUMMY_HOST_SUBSTRINGS = ("githubusercontent",)

# ローカル/無効 IP。
DUMMY_HOSTS = frozenset({"127.0.0.1", "0.0.0.0", "::1", "localhost"})

# プレースホルダー slug を含むパス（kickstarter.com/projects/example/... 等）。
DUMMY_PATH_TOKENS = (
    "/projects/example",
    "/project/example",
    "/example/",
    "/user/example",
    "/creator/example",
    "/dummy/",
    "/sample/",
    "/test/",
)


def business_url_reason(url: str | None) -> str | None:
    """営業に使えないダミー/プレースホルダー/形式不正な URL なら理由を返す（有効なら None）。

    理由は機械可読（"種別:詳細" 形式）。http/https 以外・ホスト無し・ダミーホスト・
    ローカル IP・プレースホルダー slug を弾く。
    """
    raw = (url or "").strip()
    if not raw:
        return "empty"

    try:
        parsed = urlparse(raw)
    except ValueError:
        return "format:unparseable"

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return f"scheme:{scheme or 'none'}"

    host = (parsed.hostname or "").lower()
    if not host:
        return "no_host"

    # ローカル / 無効ホスト・IP
    if host in DUMMY_HOSTS:
        return f"dummy_host:{host}"

    # ダミーホストラベル（example.com / xxxx.example.com / test.io …）
    labels = host.split(".")
    for label in labels:
        if label in DUMMY_HOST_LABELS:
            return f"dummy_host:{label}"

    # 部分一致ホスト（raw.githubusercontent.com 等）
    for sub in DUMMY_HOST_SUBSTRINGS:
        if sub in host:
            return f"blocked_host:{sub}"

    # プレースホルダー slug を含むパス（kickstarter.com/projects/example/...）
    path = (parsed.path or "").lower()
    for tok in DUMMY_PATH_TOKENS:
        if tok in path:
            return f"placeholder_path:{tok}"

    return None


def is_valid_business_url(url: str | None) -> bool:
    """営業に使える実在しうる URL かどうか（ダミー/プレースホルダー/形式不正でない）。"""
    return business_url_reason(url) is None


def filter_business_urls(urls: list[str] | None) -> list[str]:
    """有効なビジネス URL のみを残す（順序維持・重複排除）。"""
    out: list[str] = []
    seen: set[str] = set()
    for u in urls or []:
        s = (u or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        if is_valid_business_url(s):
            out.append(s)
    return out


def first_valid_url(*candidates: str | None) -> str | None:
    """優先順に並べた候補から、最初の有効なビジネス URL を返す（無ければ None）。"""
    for c in candidates:
        if c and is_valid_business_url(c):
            return c.strip()
    return None


def verify_url_ok(url: str | None, *, timeout: float = 4.0) -> bool:
    """URL が 200 OK（2xx/3xx）で到達できるかをベストエフォートで確認する。

    - 4xx/5xx（404 含む）は False（表示しない）。
    - 通信失敗・タイムアウト等の「判定不能」は True（誤除外を避けるため落とさない）。
    ダミー URL は事前に is_valid_business_url で弾く前提。
    """
    if not is_valid_business_url(url):
        return False
    try:
        import httpx

        # HEAD が拒否される（405/403）サイトも多いので GET フォールバックする。
        try:
            resp = httpx.head(url, timeout=timeout, follow_redirects=True)
            if resp.status_code == 405 or resp.status_code == 403:
                resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        except httpx.HTTPError:
            resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        return resp.status_code < 400
    except Exception as exc:  # noqa: BLE001  判定不能は落とさない
        logger.info("verify_url_ok inconclusive %s: %s", url, exc)
        return True
