"""小売店・取扱店をメーカー公式サイトとして採用しないことの検証。

30件実測で、ブランドを取り扱う販売店のサイトが verdict=official / confidence=high と
判定されていた（サイト素性が「ブランド名 | 販売店名」の形式で、ブランド名側が
メーカー名と一致してしまうため）。

安全側の設計として、降格は **ドメインがブランド名と一致しない場合にのみ** 適用する。
自社ドメインなら運営者はメーカー本人であり、小売語があっても降格しない。
gold 案件をハードコードせず一般ルールを検証する。

実行: docker compose exec -T backend python tests/test_reseller_detection.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'reseller.sqlite'}"
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


def _html(title, site_name=None, org=None):
    parts = [f"<title>{title}</title>"]
    if site_name:
        parts.append(f'<meta property="og:site_name" content="{site_name}" />')
    if org:
        parts.append(
            '<script type="application/ld+json">'
            f'{{"@type":"Organization","name":"{org}"}}</script>'
        )
    return f"<html><head>{''.join(parts)}</head><body></body></html>"


def test_brand_pipe_othername_downgraded():
    """『ブランド名 ｜ 別サイト名』かつドメイン不一致は official にしない。"""
    print("test_brand_pipe_othername_downgraded")
    v = osv.verify_candidate(
        "https://juicycat.kr/arcwave",
        _html("아크웨이브 ARCWAVE | 쥬시캣", org="쥬시캣"),
        maker_name="아크웨이브 Arcwave", product_name="Arcwave",
    )
    print(f"      verdict={v['verdict']} confidence={v['confidence']} site_role={v['site_role']}")
    check("official にしない", v["verdict"] != "official")
    check("candidate になる", v["verdict"] == "candidate")
    check("site_role=reseller_like", v["site_role"] == "reseller_like")
    check("confidence が high でない", v["confidence"] != "high")
    check("理由が記録される", any("取扱店" in r or "小売" in r for r in v["reasons"]))


def test_reseller_words_downgraded():
    """素性に小売語があり、ドメイン不一致なら降格する。"""
    print("test_reseller_words_downgraded")
    for title, label in [
        ("ARCWAVE 공식판매점", "공식판매점"),
        ("Acmebrand Authorized Dealer", "authorized dealer"),
        ("Acmebrand 正規販売店", "正規販売店"),
        ("Acmebrand 総代理店ショップ", "総代理店"),
        ("Acmebrand Official Distributor", "official distributor"),
    ]:
        v = osv.verify_candidate(
            "https://someshop.example.co.kr/p/1", _html(title, org="SomeShop"),
            maker_name=title.split()[0], product_name="X",
        )
        check(f"{label} は official にしない", v["verdict"] != "official")


def test_own_domain_not_downgraded():
    """ドメインがブランド名と一致するなら降格しない（S1 真のガード）。"""
    print("test_own_domain_not_downgraded")
    # 自社サイトに「Distributors」ページがあっても落とさない
    # 注: ドメインは "x.com"（DIRECTORY_HOSTS）に部分一致しない語を使う。
    #     既存の _match_any は部分一致のため brandx.com は directory 扱いになる（別課題）。
    v = osv.verify_candidate(
        "https://acmebrand.com/distributors",
        _html("Acmebrand | Distributor Network", org="Acmebrand"),
        maker_name="Acmebrand", product_name="Gadget",
    )
    print(f"      verdict={v['verdict']} site_role={v['site_role']}")
    check("自社ドメインは official のまま", v["verdict"] == "official")
    check("site_role=maker", v["site_role"] == "maker")


def test_official_store_not_downgraded():
    """『公式ストア/공식몰』は自社ECであり降格しない。"""
    print("test_official_store_not_downgraded")
    cases = [
        ("https://earlyance.kr/", _html("Earlyance 얼리언스 공식몰", org="얼리언스"),
         "얼리언스", "공식몰"),
        ("https://kr.xgimi.com/", _html("XGIMI Official Store KR", org="XGIMI"),
         "XGIMI", "Official Store"),
        ("https://brandy.jp/", _html("BrandY 公式オンラインストア", org="BrandY"),
         "BrandY", "公式ストア"),
    ]
    for url, html, maker, why in cases:
        v = osv.verify_candidate(url, html, maker_name=maker, product_name="X")
        check(f"{why} は official のまま", v["verdict"] == "official")


def test_has_reseller_hint():
    print("test_has_reseller_hint")
    check("'공식판매점' は小売", osv.has_reseller_hint("ARCWAVE 공식판매점"))
    check("'공식몰' は小売でない", not osv.has_reseller_hint("얼리언스 공식몰"))
    check("'Official Store' は小売でない", not osv.has_reseller_hint("XGIMI Official Store KR"))
    check("'Authorized Dealer' は小売", osv.has_reseller_hint("Acmebrand Authorized Dealer"))
    check("'正規販売店' は小売", osv.has_reseller_hint("Acmebrand 正規販売店"))
    check("None は False", not osv.has_reseller_hint(None))
    check("無関係文字列は False", not osv.has_reseller_hint("Acmebrand Technology Inc."))


def test_looks_reseller_page():
    print("test_looks_reseller_page")
    r = osv.looks_reseller_page(["아크웨이브 ARCWAVE | 쥬시캣"], "아크웨이브")
    check("区切り形式を検出", r is not None)
    r2 = osv.looks_reseller_page(["Acmebrand Technology"], "Acmebrand")
    check("区切りなし・小売語なしは None", r2 is None)
    r3 = osv.looks_reseller_page(["Acmebrand | Acmebrand Global"], "Acmebrand")
    check("全セグメントがブランド名なら None", r3 is None)


def test_no_regression_normal_official():
    """通常の公式サイト判定が壊れないこと。"""
    print("test_no_regression_normal_official")
    v = osv.verify_candidate(
        "https://relodin.com/",
        _html("RELOD — Open-Ear Audio Technology", site_name="RELOD AUDIO STORE",
              org="RELOD"),
        maker_name="RELOD", product_name="OVO Air",
    )
    check("公式サイトは official", v["verdict"] == "official")
    check("site_role=maker", v["site_role"] == "maker")

    # ブログは PR-D のガードで先に rejected（PR-E で壊れていないこと）
    vb = osv.verify_candidate("https://x.tistory.com/1", _html("Acmebrand 리뷰", org="Acmebrand"),
                              maker_name="Acmebrand", product_name="X")
    check("ブログは rejected のまま", vb["verdict"] == "rejected")
    check("ブログ site_role=blog", vb["site_role"] == "blog")


def main():
    test_brand_pipe_othername_downgraded()
    test_reseller_words_downgraded()
    test_own_domain_not_downgraded()
    test_official_store_not_downgraded()
    test_has_reseller_hint()
    test_looks_reseller_page()
    test_no_regression_normal_official()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
