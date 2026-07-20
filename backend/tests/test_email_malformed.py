"""不正形式メール（ドメインが www. で始まる架空アドレス）の除外を検証する。

実データで観測された `video@www.thehottiehotcomb.com` は実在しないアドレスで、
本文の語（"video"）と近傍の URL ホスト（www.thehottiehotcomb.com）が抽出側の
難読化復元経路で誤接着されて生まれる。共通検証層（email_validation）で止血する。

抽出側の正規表現（_SPLIT_EMAIL_RE）は本テストの対象外＝変更しない。正当な
難読化復元（user</span>@<span>brand.io → user@brand.io）が無傷であることも検証する。

外部 API 非依存。実行（backend ディレクトリで）:
    python tests/test_email_malformed.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402
from app.services.email_validation import (  # noqa: E402
    business_email_reason,
    is_valid_business_email,
)

_p = _f = 0


def check(name, cond):
    global _p, _f
    if cond:
        _p += 1; print(f"  ok  - {name}")
    else:
        _f += 1; print(f"  FAIL- {name}")


# ---------------- 1. www. ドメインの除外（共通検証層） ----------------
def test_www_domain_rejected():
    print("test_www_domain_rejected")
    check("info@www.example.com が除外される",
          not is_valid_business_email("info@www.example.com"))
    check("video@www.thehottiehotcomb.com が除外される",
          not is_valid_business_email("video@www.thehottiehotcomb.com"))
    check("除外理由が format:www_domain",
          business_email_reason("video@www.thehottiehotcomb.com") == "format:www_domain")
    check("大文字/前後空白でも除外（正規化後に判定）",
          business_email_reason("  Video@WWW.TheHottieHotComb.com  ") == "format:www_domain")
    check("サブドメイン付き www も除外",
          business_email_reason("a@www.shop.brand.io") == "format:www_domain")


# ---------------- 2. 正当メールを誤除外しないこと ----------------
def test_legitimate_not_rejected():
    print("test_legitimate_not_rejected")
    check("user@wwwtech.com は通る", is_valid_business_email("user@wwwtech.com"))
    check("user@x.wwwtech.com は通る", is_valid_business_email("user@x.wwwtech.com"))
    check("user@brand.io は通る", is_valid_business_email("user@brand.io"))
    check("user@mywww.brand.io は通る", is_valid_business_email("user@mywww.brand.io"))
    # user@mywww.example.com は既存の dummy_domain:example 規則で落ちる（本修正とは無関係）。
    # 本修正が www 規則で誤って掴んでいないことを理由名で確認する。
    check("user@mywww.example.com は www 規則では落ちない",
          business_email_reason("user@mywww.example.com") == "dummy_domain:example")
    # 既存の理由名が変わっていないこと（回帰）
    check("a@example.com の理由は dummy_domain 維持",
          business_email_reason("a@example.com") == "dummy_domain:example")
    check("dummy@brand.com の理由は dummy_local 維持",
          business_email_reason("dummy@brand.com") == "dummy_local:dummy")


# ---------------- 3. 難読化復元が壊れていないこと ----------------
def test_split_obfuscation_still_works():
    print("test_split_obfuscation_still_works")
    html = '<p>Contact: user</span>@<span>brand.io</p>'
    check("user</span>@<span>brand.io が user@brand.io に復元される",
          "user@brand.io" in cds.deobfuscate_emails(html))
    check("復元された user@brand.io が採用される",
          "user@brand.io" in cds.extract_emails(html))
    # [at]/[dot] 難読化も無傷
    check("support [at] brand [dot] io の復元が無傷",
          "support@brand.io" in cds.extract_emails("<p>support [at] brand [dot] io</p>"))


# ---------------- 4. 抽出 end-to-end で最終採用されないこと ----------------
# 要求された HTML 断片。注意: この断片には '@' が無いため誤接着は発生せず、
# 修正前から malformed email は生まれない（下の _GLUE_HTML が実際の再現ケース）。
_REQUESTED_HTML = (
    "<p>Watch the video</p>\n"
    '<a href="https://www.thehottiehotcomb.com">\n'
    "  www.thehottiehotcomb.com\n"
    "</a>"
)

# 実際に誤接着を再現する断片（本文の語と URL ホストの間に '@' が挟まる形。
# SNS ハンドル表記や "動画は @www.brand.com" のような本文で実在する）。
_GLUE_HTML = "<p>Watch the video</p> @<a>www.thehottiehotcomb.com</a>"


def test_extraction_end_to_end():
    print("test_extraction_end_to_end")
    for label, html in (("要求断片", _REQUESTED_HTML), ("誤接着再現断片", _GLUE_HTML)):
        got = cds.extract_emails(html)
        check(f"{label}: malformed email が最終採用されない",
              "video@www.thehottiehotcomb.com" not in got)
        check(f"{label}: www. ドメインのアドレスが一切残らない",
              not [e for e in got if e.split("@", 1)[-1].lower().startswith("www.")])

    # 誤接着断片では抽出候補としては生成されるが、検証層で落ちることを明示する
    # （＝止血が抽出側ではなく共通検証層で効いている証明）。
    check("誤接着候補は deobfuscate では生成される（抽出側は未変更）",
          "video@www.thehottiehotcomb.com" in cds.deobfuscate_emails(_GLUE_HTML))
    _, excluded = cds.extract_emails_with_reasons(_GLUE_HTML)
    check("extract_emails_with_reasons が理由 format:www_domain を返す",
          any(x["email"].lower() == "video@www.thehottiehotcomb.com"
              and x["reason"] == "format:www_domain" for x in excluded))


# ---------------- 5. email_exclusion_reason 経由でも伝播すること ----------------
def test_exclusion_reason_propagates():
    print("test_exclusion_reason_propagates")
    # email_exclusion_reason は shared_reason を prefix で選別して転送するため、
    # "format" 始まりでないと伝播しない。その契約を固定する。
    check("email_exclusion_reason が www ドメインを除外",
          cds.email_exclusion_reason("video@www.thehottiehotcomb.com") == "format:www_domain")
    check("正当ドメインは email_exclusion_reason を通る",
          cds.email_exclusion_reason("user@wwwtech.com") is None)


def main():
    test_www_domain_rejected()
    test_legitimate_not_rejected()
    test_split_obfuscation_still_works()
    test_extraction_end_to_end()
    test_exclusion_reason_propagates()
    print(f"\n{_p} passed / {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    sys.exit(main())
