"""メール抽出・復号・正規化の fixture テスト（抽出器の正しさを固定）。

標準的な難読化 15 パターンに加え、**実案件（Wadiz browser capture）由来の実データ**を
含める（tests/contact_intel_eval/wadiz_capture_fixtures.json）。これは合成のみでの実用性
証明ではなく、抽出器ロジックの回帰防止が目的。実用性は baseline.py / harness.py で別途測る。

実行: docker exec cfagent-backend python tests/test_email_extraction_fixtures.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(tempfile.gettempdir(), 'email_fx.sqlite')}"
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402

_passed = 0
_failed = 0
_fail_names: list[str] = []


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        _fail_names.append(name)
        print(f"  FAIL- {name}")


def cf_encode(email: str, key: int = 0x42) -> str:
    """Cloudflare Email Protection の data-cfemail 16 進文字列を作る（テスト用）。"""
    b = [key] + [ord(c) ^ key for c in email]
    return "".join(f"{x:02x}" for x in b)


def extracted(html: str, site_domain: str | None = None) -> set[str]:
    return {e.lower() for e in cds.extract_emails(html, site_domain)}


def test_standard_obfuscation():
    print("test_standard_obfuscation")
    cases = [
        ("visible text", "Contact us at hello@brandx.io anytime", {"hello@brandx.io"}),
        ("mailto", '<a href="mailto:sales@brandx.io">mail</a>', {"sales@brandx.io"}),
        ("full-width @", "info＠brandx.io", {"info@brandx.io"}),
        ("[at][dot]", "info [at] brandx [dot] io", {"info@brandx.io"}),
        ("(at)(dot)", "info(at)brandx(dot)io", {"info@brandx.io"}),
        ("html entity @ .", "info&#64;brandx&#46;io", {"info@brandx.io"}),
        ("&commat;", "info&commat;brandx.io", {"info@brandx.io"}),
        ("json-ld email",
         '<script type="application/ld+json">{"email":"jsonld@brandx.io"}</script>',
         {"jsonld@brandx.io"}),
        ("script var", '<script>var e = "script@brandx.io";</script>', {"script@brandx.io"}),
        ("cfemail attr",
         f'<a class="__cf_email__" data-cfemail="{cf_encode("cf@brandx.io")}">[email protected]</a>',
         {"cf@brandx.io"}),
        ("footer placement",
         '<footer><p>Email: team@brandx.io</p></footer>', {"team@brandx.io"}),
        ("span-split",
         'Reach <span>user</span>@<span>brandx.io</span> now', {"user@brandx.io"}),
        ("url-encoded mailto",
         '<a href="mailto:enc%40brandx.io">x</a>', {"enc@brandx.io"}),
        ("zero-width",
         "info​@brandx​.io", {"info@brandx.io"}),
    ]
    for name, html, expected in cases:
        got = extracted(html)
        check(f"抽出: {name} -> {expected}", expected.issubset(got))


def test_provider_and_platform_rules():
    print("test_provider_and_platform_rules")
    # Gmail / Naver / Daum / Kakao / Outlook / Yahoo / 独自ドメインは除外しない
    for provider in ("maker@gmail.com", "maker@naver.com", "maker@daum.net",
                     "maker@kakao.com", "maker@outlook.com", "maker@yahoo.co.jp",
                     "hello@own-brand.co.kr"):
        got = extracted(f"Contact: {provider}")
        check(f"誤除外しない: {provider}", provider.lower() in got)
    # プラットフォーム運営メールは（案件の source_site と一致する場合）除外
    got = extracted("문의 maker@brandk.co.kr / support@wadiz.kr",
                    cds.source_site_email_domain("wadiz"))
    check("wadiz 運営メール除外", "support@wadiz.kr" not in got)
    check("メーカーメールは残す", "maker@brandk.co.kr" in got)


def test_real_wadiz_capture():
    print("test_real_wadiz_capture")
    fx = Path(__file__).resolve().parent / "contact_intel_eval" / "wadiz_capture_fixtures.json"
    if not fx.exists():
        # 実データ fixture は再生成可能（build_gold_candidates.py）。git には入れない
        # ため、無ければこのセクションはスキップ（合成の難読化テストは別途カバー）。
        print("  skip - wadiz 実データ fixture 不在（build_gold_candidates で生成）")
        return
    from app.services import source_ownership as so

    records = json.loads(fx.read_text(encoding="utf-8"))
    site_domain = cds.source_site_email_domain("wadiz")
    # evidence テキストごとに value（実メール）を所有者分類で検証する。maker 直通は direct
    # に出て運営(support@wadiz.kr)は出ない。代理店(brand-kr.com 等)は maker 正式メールに
    # せず、fallback として保持されること（＝完全消去しない）を確認する。
    for r in records:
        ev = r.get("evidence") or ""
        val = (r.get("value") or "").lower()
        if not ev or not val:
            continue
        got = extracted(ev, site_domain)
        cls = cds.extract_emails_classified(ev, source_site_domain=site_domain)
        direct = {d["email"] for d in cls["direct"]}
        fallback = {d["email"] for d in cls["fallback"]}
        own = so.classify_domain(val).ownership_class
        if own in ("agency", "distributor"):
            # 代理店/販売窓口: maker 正式メールにしない（direct 非採用）が fallback で保持。
            check(f"実Wadiz代理店 direct非採用 p{r['project_id']}: {val}", val not in got)
            check(f"実Wadiz代理店 fallback保持 p{r['project_id']}: {val}", val in fallback)
        elif own in ("crowdfunding_platform", "crowdfunding_marketing_service",
                     "url_shortener", "messenger", "retailer", "unrelated_company"):
            # 運営/販促/短縮/小売等: direct にも fallback にも入れない。
            check(f"実Wadiz第三者除外 p{r['project_id']}: {val}", val not in got and val not in fallback)
        else:
            # maker 直通/個人/unknown: 従来どおり抽出（direct 側で拾う）。
            check(f"実Wadiz抽出 p{r['project_id']}: {val}", val in got)
        check(f"実Wadiz運営除外 p{r['project_id']}", "support@wadiz.kr" not in got)


def test_excluded_reason_tracking():
    print("test_excluded_reason_tracking")
    html = ("문의 maker@brandk.co.kr / support@wadiz.kr / noreply@brandk.co.kr / "
            "hello@gmail.com")
    accepted, excluded = cds.extract_emails_with_reasons(
        html, cds.source_site_email_domain("wadiz"))
    acc = {e.lower() for e in accepted}
    exmap = {e["email"].lower(): e["reason"] for e in excluded}
    check("メーカーメールは accepted", "maker@brandk.co.kr" in acc)
    check("Gmail は accepted（誤除外しない）", "hello@gmail.com" in acc)
    check("運営メールは excluded", "support@wadiz.kr" in exmap)
    check("no-reply は excluded", "noreply@brandk.co.kr" in exmap)
    check("excluded に理由が付く", all(exmap.values()))


def main():
    test_standard_obfuscation()
    test_provider_and_platform_rules()
    test_real_wadiz_capture()
    test_excluded_reason_tracking()
    print(f"\n{_passed} passed, {_failed} failed")
    if _fail_names:
        print("FAILURES:", _fail_names)
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
