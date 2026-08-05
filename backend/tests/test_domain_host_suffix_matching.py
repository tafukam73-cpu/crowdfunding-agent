"""ホスト判定を部分一致から厳密照合へ変えたことの検証。

旧実装は ``h in host`` の部分一致だったため、DIRECTORY_HOSTS の "x.com"（Twitter/X）が
``brandx.com`` / ``matrix.com`` / ``lumix.com`` などに誤ヒットし、正当なメーカー
公式サイトをディレクトリとして棄却していた。

一方でエントリには 3 種類の意図が混在する。一律にサフィックス一致へ置き換えると
``amazon.`` のようなブランド指定が壊れ、Amazon / 楽天 / eBay が
マーケットプレイスとして検出されなくなる（今より重い退行）。
本テストは「誤ヒットが消えること」と「本来ブロックすべきものが残ること」を
同時に固定する。gold 案件をハードコードせず一般ルールを検証する。

実行: docker compose exec -T backend python tests/test_domain_host_suffix_matching.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'host_match.sqlite'}"
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


def blocked(url: str) -> list[str]:
    """いずれかの棄却判定に該当したリスト名を返す。"""
    return [
        n for n, f in (
            ("marketplace", osv.is_marketplace),
            ("directory", osv.is_directory),
            ("news", osv.is_news),
            ("blog", osv.is_blog_platform),
        ) if f(url)
    ]


def test_x_com_and_social_still_blocked():
    """SNS 本体は引き続き棄却されること。"""
    print("test_x_com_and_social_still_blocked")
    for host in ("x.com", "www.x.com", "mobile.x.com",
                 "twitter.com", "sub.twitter.com"):
        check(f"{host} は directory", osv.is_directory(f"https://{host}/foo"))


def test_lookalike_domains_not_blocked():
    """末尾に一致するだけの別ドメインを棄却しないこと（本バグの回帰テスト）。"""
    print("test_lookalike_domains_not_blocked")
    for host in ("brandx.com", "matrix.com", "phoenix.com", "examplex.com",
                 "xcommerce.com", "nottwitter.com", "lumix.com", "helix.com",
                 "onyx.com", "sonix.com", "mymedium.com", "freebay.com",
                 "bigyelp.com", "mywix.com"):
        hits = blocked(f"https://{host}/")
        check(f"{host} は棄却しない（実際: {hits or 'なし'}）", not hits)


def test_marketplace_still_blocked():
    """ブランド指定（任意 TLD）が壊れていないこと。最重要の退行防止。"""
    print("test_marketplace_still_blocked")
    for url in ("https://www.amazon.co.jp/dp/X", "https://www.amazon.com/dp/X",
                "https://www.amazon.de/dp/X", "https://item.rakuten.co.jp/x/",
                "https://www.ebay.com/itm/1", "https://www.ebay.co.uk/itm/1",
                "https://shopee.tw/x", "https://www.coupang.com/x",
                "https://gmarket.co.kr/x", "https://www.11st.co.kr/x",
                "https://www.qoo10.jp/x", "https://www.aliexpress.com/item/1",
                "https://x.taobao.com/", "https://www.etsy.com/listing/1"):
        check(f"marketplace: {url[:44]}", osv.is_marketplace(url))


def test_hostprefix_still_blocked():
    """ホスト先頭ラベル指定が壊れていないこと。"""
    print("test_hostprefix_still_blocked")
    check("news.yahoo.co.jp は news", osv.is_news("https://news.yahoo.co.jp/articles/x"))
    check("news.mynavi.jp は news", osv.is_news("https://news.mynavi.jp/article/1"))
    check("24h.pchome.com.tw は marketplace",
          osv.is_marketplace("https://24h.pchome.com.tw/prod/X"))
    check("tw.buy.yahoo.com は marketplace",
          osv.is_marketplace("https://tw.buy.yahoo.com/item/1"))
    check("shop.line.me は marketplace", osv.is_marketplace("https://shop.line.me/x"))
    # "news" を含むだけの別ドメインは news 扱いしない
    check("newsly.com は news でない", not osv.is_news("https://newsly.com/"))
    check("technewsworld.com は news でない",
          not osv.is_news("https://technewsworld.com/"))


def test_brand_rule_precision():
    """ブランド指定が別ブランドへ波及しないこと。"""
    print("test_brand_rule_precision")
    check("freebay.com は marketplace でない", not osv.is_marketplace("https://freebay.com/"))
    check("bigyelp.com は directory でない", not osv.is_directory("https://bigyelp.com/"))
    check("glassdoor.com は directory", osv.is_directory("https://www.glassdoor.com/x"))
    check("glassdoor.co.uk は directory", osv.is_directory("https://www.glassdoor.co.uk/x"))
    check("myglassdoor.com は directory でない",
          not osv.is_directory("https://myglassdoor.com/"))


def test_existing_normal_hosts():
    """既存の正常系が変わらないこと。"""
    print("test_existing_normal_hosts")
    check("relodin.com はどれにも該当しない", not blocked("https://relodin.com/"))
    check("kickstarter.com は棄却リスト対象外",
          not blocked("https://www.kickstarter.com/projects/a/b"))
    check("blog.naver.com は blog", osv.is_blog_platform("https://m.blog.naver.com/a/1"))
    check("tistory.com は blog", osv.is_blog_platform("https://foo.tistory.com/1"))
    check("blog.mycompany.com は blog でない",
          not osv.is_blog_platform("https://blog.mycompany.com/1"))


def test_host_normalization():
    """ホスト正規化（大文字/末尾ドット/ポート/不正URL）。"""
    print("test_host_normalization")
    check("大文字 X.COM", osv.is_directory("https://X.COM/foo"))
    check("大文字 WWW.Twitter.Com", osv.is_directory("https://WWW.Twitter.Com/foo"))
    check("末尾ドット x.com.", osv.is_directory("https://x.com./foo"))
    check("ポート付き x.com:8443", osv.is_directory("https://x.com:8443/foo"))
    check("大文字+ポート AMAZON.CO.JP:443",
          osv.is_marketplace("https://AMAZON.CO.JP:443/dp/X"))
    check("末尾ドット brandx.com. は棄却しない",
          not osv.is_directory("https://brandx.com./"))
    # 不正・空 URL は安全側（棄却しない）
    for bad in ("", None, "not a url", "http://", "javascript:alert(1)", "///"):
        check(f"不正URL {bad!r} は棄却しない", not blocked(bad))


def test_host_helper():
    print("test_host_helper")
    check("_host は小文字化", osv._host("https://X.COM/") == "x.com")
    check("_host は www. を落とす", osv._host("https://www.example.com/") == "example.com")
    check("_host はポートを落とす", osv._host("https://example.com:8080/") == "example.com")
    check("_host は末尾ドットを落とす", osv._host("https://example.com./") == "example.com")
    check("_host は認証情報を落とす",
          osv._host("https://user:pw@example.com/") == "example.com")
    check("_host は空URLで空文字", osv._host("") == "")


def test_rules_cover_legacy_lists():
    """後方互換の旧定数が規則から正しく導出されていること。"""
    print("test_rules_cover_legacy_lists")
    check("MARKETPLACE_HOSTS は 32 件", len(osv.MARKETPLACE_HOSTS) == 32)
    check("NEWS_HOSTS は 23 件", len(osv.NEWS_HOSTS) == 23)
    check("DIRECTORY_HOSTS は 33 件", len(osv.DIRECTORY_HOSTS) == 33)
    check("合計 88 件",
          len(osv.MARKETPLACE_HOSTS) + len(osv.NEWS_HOSTS) + len(osv.DIRECTORY_HOSTS) == 88)
    check("旧 amazon. が復元される", "amazon." in osv.MARKETPLACE_HOSTS)
    check("旧 x.com が復元される", "x.com" in osv.DIRECTORY_HOSTS)
    check("旧 news. が復元される", "news." in osv.NEWS_HOSTS)
    check("旧 24h.pchome が復元される", "24h.pchome" in osv.MARKETPLACE_HOSTS)
    kinds = {k for rules in (osv.MARKETPLACE_RULES, osv.NEWS_RULES, osv.DIRECTORY_RULES)
             for k, _ in rules}
    check("規則種別は3種のみ", kinds <= {"domain", "brand", "hostprefix"})


def test_verify_candidate_integration():
    """verify_candidate 経由でも誤棄却が消えること。"""
    print("test_verify_candidate_integration")
    html = ("<html><head><title>Lumix Devices</title>"
            '<script type="application/ld+json">'
            '{"@type":"Organization","name":"Lumix Devices"}</script>'
            "</head><body></body></html>")
    v = osv.verify_candidate("https://lumix.com/", html,
                             maker_name="Lumix Devices", product_name="Camera")
    print(f"      verdict={v['verdict']} site_role={v['site_role']}")
    check("lumix.com が rejected されない", v["verdict"] != "rejected")
    check("official と判定される", v["verdict"] == "official")

    v2 = osv.verify_candidate("https://x.com/lumix", html,
                              maker_name="Lumix Devices", product_name="Camera")
    check("x.com は引き続き rejected", v2["verdict"] == "rejected")


def main():
    test_x_com_and_social_still_blocked()
    test_lookalike_domains_not_blocked()
    test_marketplace_still_blocked()
    test_hostprefix_still_blocked()
    test_brand_rule_precision()
    test_existing_normal_hosts()
    test_host_normalization()
    test_host_helper()
    test_rules_cover_legacy_lists()
    test_verify_candidate_integration()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
