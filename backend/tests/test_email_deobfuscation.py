"""メール抽出・難読化解除のオフライン検証（ネットワーク/DB 不要）。

営業メール発見率改善のため、HTML からの抽出を多様な難読化に対して検証する：
  - mailto:
  - 素の user@domain（本文 / JSON-LD / JS 内の文字列）
  - Cloudflare Email Protection（data-cfemail 属性 / /cdn-cgi/l/email-protection#hex）
  - テキスト難読化（[at] / (at) / ＠ / [dot] / (dot) / HTML entity）
  - JavaScript 文字列連結（"info" + "@" + "brand.com" / ドメイン分割）
誤検出（非メールの連結や画像/アセット）や、ダミー/プラットフォームの除外も確認する。

実行（backend ディレクトリで）:
    python tests/test_email_deobfuscation.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# import 連鎖で DB 設定が必要になるため隔離 sqlite を指定（実際には DB は使わない）。
os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{os.path.join(tempfile.gettempdir(), 'email_deobf_test.sqlite')}",
)
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.services import contact_discovery_service as cds  # noqa: E402

_passed = 0
_failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def _has(html: str, email: str) -> bool:
    return email in cds.extract_emails(html)


def _cf_encode(email: str, key: int = 0x7A) -> str:
    """Cloudflare Email Protection 形式（先頭バイト=XOR鍵）にエンコードする。"""
    out = format(key, "02x")
    for ch in email:
        out += format(ord(ch) ^ key, "02x")
    return out


def test_plain_and_mailto():
    print("test_plain_and_mailto")
    check("mailto: を抽出", _has('<a href="mailto:hi@shopbrand.co">m</a>', "hi@shopbrand.co"))
    check("本文の素メールを抽出", _has("Contact: sales@makerbrand.com today", "sales@makerbrand.com"))
    check("JSON-LD 内のメールを抽出",
          _has('<script type="application/ld+json">{"email":"press@makerbrand.com"}</script>',
               "press@makerbrand.com"))
    check("JS 内の素メールを抽出",
          _has('<script>var e="team@makerbrand.com";</script>', "team@makerbrand.com"))


def test_cloudflare():
    print("test_cloudflare")
    enc = _cf_encode("sales@makerbrand.com")
    check("data-cfemail 属性を復号",
          _has(f'<span data-cfemail="{enc}">[email protected]</span>', "sales@makerbrand.com"))
    check("/cdn-cgi/l/email-protection#hex を復号",
          _has(f'<a href="/cdn-cgi/l/email-protection#{enc}">protected</a>',
               "sales@makerbrand.com"))


def test_text_obfuscation():
    print("test_text_obfuscation")
    check("[at] / [dot]", _has("press [at] makerbrand [dot] com", "press@makerbrand.com"))
    check("(at) / (dot)", _has("hello (at) makerbrand (dot) com", "hello@makerbrand.com"))
    check("全角 ＠", _has("info ＠ makerbrand.com", "info@makerbrand.com"))
    check("HTML entity &#64; &#46;",
          _has("info &#64; makerbrand &#46; com", "info@makerbrand.com"))


def test_js_concatenation():
    print("test_js_concatenation")
    check('"info" + "@" + "brand.com"',
          _has('<script>var e = "info" + "@" + "brandco.com";</script>', "info@brandco.com"))
    check('ドメイン分割 "@" + "brand" + "." + "com"',
          _has('var e = "hello" + "@" + "brandco" + "." + "com";', "hello@brandco.com"))
    check("シングルクォートの連結",
          _has("var e = 'team' + '@' + 'brandco.com';", "team@brandco.com"))


def test_no_false_positive():
    print("test_no_false_positive")
    check("@ を含まない連結は拾わない",
          cds.extract_emails('<script>var a="foo"+"bar"+"baz";</script>') == [])
    check("画像アセットの @-風は拾わない",
          "logo@2x.png" not in cds.extract_emails('<img src="logo@2x.png">'))


def test_exclusions():
    print("test_exclusions")
    check("example ダミードメインを除外",
          cds.extract_emails("mail me at hi@example.com") == [])
    check("acme プレースホルダを除外",
          cds.extract_emails("hi@acme.com here") == [])
    check("no-reply を除外",
          cds.extract_emails("no-reply@makerbrand.com") == [])


def main():
    test_plain_and_mailto()
    test_cloudflare()
    test_text_obfuscation()
    test_js_concatenation()
    test_no_false_positive()
    test_exclusions()
    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
