"""ブログプラットフォーム上の記事を公式サイトとして採用しないことの検証。

30件実測で、Tistory / Naver ブログの記事が verdict=official / confidence=high に
なっていた。記事本文にメーカー名も商品名も出るため素性一致してしまうのが原因。
独自ドメインの企業ブログ（blog.example.com）は巻き込まないこと（部分一致の禁止）も
あわせて検証する。gold 案件をハードコードせず一般ルールを検証する。

実行: docker compose exec -T backend python tests/test_blog_platform_exclusion.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'blog_excl.sqlite'}"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import official_site_verifier as osv  # noqa: E402

_passed = _failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


# メーカー名も商品名も JSON-LD も揃った「一見 official に見える」ブログ記事。
_BLOGGY_HTML = """
<html><head>
<title>유그린 NAS로 집에서 나만의 데이터 클라우드 구축하기</title>
<meta property="og:site_name" content="유그린 NAS 후기" />
<script type="application/ld+json">
{"@type":"Organization","name":"유그린"}
</script>
</head><body>유그린 NAS 리뷰</body></html>
"""


def test_blog_hosts_detected():
    print("test_blog_hosts_detected")
    cases = [
        "https://e-conomyfree.tistory.com/79",
        "https://m.blog.naver.com/wj_2169/222995428665",
        "https://blog.naver.com/someone/123",
        "https://example.blogspot.com/2026/01/post.html",
        "https://medium.com/@someone/article-123",
        "https://note.com/someone/n/abc123",
        "https://ameblo.jp/someone/entry-123.html",
        "https://someone.hatenablog.com/entry/2026/01/01",
        "https://someone.wordpress.com/2026/01/01/post/",
        "https://someone.tumblr.com/post/123",
        "https://someone.substack.com/p/article",
        "https://brunch.co.kr/@someone/1",
    ]
    for url in cases:
        check(f"blog 判定: {url[:52]}", osv.is_blog_platform(url))


def test_own_domain_blog_not_excluded():
    """独自ドメインの企業ブログは除外しない（部分一致にしない）。"""
    print("test_own_domain_blog_not_excluded")
    for url in [
        "https://blog.mycompany.com/news/1",
        "https://mycompany.com/blog/1",
        "https://news.brandsite.co.jp/blog",
        "https://relodin.com/blogs/news",
        # サフィックス誤爆の確認（"tistory.com" を含むが別ドメイン）
        "https://nottistory.com/",
        "https://mytistory.com.example.org/",
    ]:
        check(f"blog 扱いしない: {url[:52]}", not osv.is_blog_platform(url))


def test_verify_candidate_rejects_blog():
    """素性が揃っていても official にしないこと。"""
    print("test_verify_candidate_rejects_blog")
    v = osv.verify_candidate(
        "https://e-conomyfree.tistory.com/79", _BLOGGY_HTML,
        maker_name="유그린", product_name="NAS",
    )
    print(f"      verdict={v['verdict']} confidence={v['confidence']} site_role={v['site_role']}")
    check("verdict=rejected", v["verdict"] == "rejected")
    check("site_role=blog", v["site_role"] == "blog")
    check("confidence が high でない", v["confidence"] != "high")
    check("理由が記録される", any("ブログ" in r for r in v["reasons"]))

    v2 = osv.verify_candidate(
        "https://m.blog.naver.com/wj_2169/222995428665", _BLOGGY_HTML,
        maker_name="놀로", product_name="노즈워크",
    )
    check("Naver ブログも rejected", v2["verdict"] == "rejected")
    check("Naver ブログ site_role=blog", v2["site_role"] == "blog")


def test_real_official_site_unaffected():
    """通常の公式サイト判定は変わらないこと（回帰防止）。"""
    print("test_real_official_site_unaffected")
    html = """
    <html><head>
    <title>RELOD — Open-Ear Audio Technology</title>
    <meta property="og:site_name" content="RELOD AUDIO STORE" />
    <script type="application/ld+json">{"@type":"Organization","name":"RELOD"}</script>
    </head><body></body></html>
    """
    v = osv.verify_candidate("https://relodin.com/", html,
                             maker_name="RELOD", product_name="OVO Air")
    print(f"      verdict={v['verdict']} confidence={v['confidence']} site_role={v['site_role']}")
    check("公式サイトは official のまま", v["verdict"] == "official")
    check("site_role=maker", v["site_role"] == "maker")

    # 独自ドメインのブログ配下でも、素性が一致すれば従来どおり判定される
    v2 = osv.verify_candidate("https://blog.relodin.com/news/1", html,
                              maker_name="RELOD", product_name="OVO Air")
    check("独自ドメインのブログは rejected にしない", v2["verdict"] != "rejected")


def test_site_role_field_present():
    print("test_site_role_field_present")
    v = osv.verify_candidate("https://amazon.co.jp/dp/X", "<html></html>",
                             maker_name="X", product_name="Y")
    check("marketplace でも site_role キーがある", "site_role" in v)
    check("marketplace は rejected", v["verdict"] == "rejected")


def main():
    test_blog_hosts_detected()
    test_own_domain_blog_not_excluded()
    test_verify_candidate_rejects_blog()
    test_real_official_site_unaffected()
    test_site_role_field_present()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
