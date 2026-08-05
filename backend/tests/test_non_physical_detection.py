"""contact_search_gate の非物理商品判定（単語境界＋文脈）の検証。

従来は部分一致だったため "companion app with ..." を持つヘッドホンが
「物理商品ではない企画」と誤判定されていた。単語境界での照合と、
物理商品を示す語による打ち消しが正しく働くことを検証する。
特定案件 ID や商品名で分岐しない一般ルールのテスト。

実行: docker compose exec -T backend python tests/test_non_physical_detection.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'non_physical.sqlite'}"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import contact_search_gate as g  # noqa: E402

_passed = _failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


def test_word_boundary():
    """部分一致による誤爆が無いこと。"""
    print("test_word_boundary")
    check("'app' は 'application' に一致しない", not g._has_term("this application", "app"))
    check("'app' は 'happy' に一致しない", not g._has_term("a happy day", "app"))
    check("'app' は 'companion app with' に一致する", g._has_term("companion app with eq", "app"))
    check("'event' は 'eventually' に一致しない", not g._has_term("eventually ships", "event"))
    check("'book' は 'notebook' に一致しない", not g._has_term("a notebook stand", "book"))
    check("'book' は 'bookshelf' に一致しない", not g._has_term("bookshelf speaker", "book"))
    check("'game' は 'gamer' に一致しない", not g._has_term("for gamers", "game"))
    check("'film' は 'filmmaking' に一致しない", not g._has_term("filmmaking rig", "film"))
    check("日本語は部分一致のまま", g._has_term("この映画について", "映画"))


def test_physical_product_with_companion_app():
    """物理商品＋付随アプリを非物理と誤判定しないこと（本件の回帰テスト）。"""
    print("test_physical_product_with_companion_app")
    cases = [
        "ovo air 2: open-air headphone that lets your ears breathe "
        "companion app with custom equalizer, spatial 3d audio, smart finder sound",
        "smart watch with companion app and heart rate sensor",
        "bluetooth speaker with app control and rechargeable battery",
        "gaming headset with 7.1 surround for game night",
        "bookshelf speaker made of aluminum",
        "portable music player with titanium body",
        "camera gimbal, film your adventures, waterproof",
    ]
    for t in cases:
        check(f"非物理でない: {t[:46]}...", not g.is_non_physical(t))


def test_true_non_physical_still_detected():
    """本当の非物理企画は従来どおり検出すること。"""
    print("test_true_non_physical_still_detected")
    cases = [
        ("mobile app for language learning", "mobile app"),
        ("a saas platform for teams", "saas"),
        ("our documentary about the ocean", "documentary"),
        ("a board game for four players", "board game"),
        ("donation drive for the shelter", "donation"),
        ("charity fundraiser event", "charity"),
        ("online course and membership", "membership"),
        ("新しいアプリを開発します", "アプリ"),
        ("映画制作の支援をお願いします", "映画"),
        ("寄付を募っています", "寄付"),
    ]
    for t, why in cases:
        check(f"非物理と判定: {why}", g.is_non_physical(t))


def test_weak_hint_without_physical_context():
    """WEAK 語のみ・物理語なしなら非物理と判定すること。"""
    print("test_weak_hint_without_physical_context")
    check("'a game about cats' は非物理", g.is_non_physical("a game about cats"))
    check("'our new album' は非物理", g.is_non_physical("our new album is coming"))
    check("'a novel by the author' は非物理", g.is_non_physical("a novel by the author"))


def test_excluded_categories_integration():
    """_excluded_categories 経由でも同じ結果になること。"""
    print("test_excluded_categories_integration")
    physical = ("open-air headphone companion app with custom equalizer "
                "rechargeable battery bluetooth")
    reasons = g._excluded_categories(physical)
    check("ヘッドホンに非物理理由が付かない",
          not any("物理商品ではない" in r for r in reasons))

    non_physical = "mobile app for meditation and mindfulness"
    reasons2 = g._excluded_categories(non_physical)
    check("mobile app には非物理理由が付く",
          any("物理商品ではない" in r for r in reasons2))


def test_japanese_companion_app_is_physical():
    """日本語「アプリ連動」等を持つ物理商品を非物理と誤判定しないこと。

    実案件 #4「スマート水耕栽培キット（アプリ連動）」が誤判定された回帰テスト。
    """
    print("test_japanese_companion_app_is_physical")
    cases = [
        "スマート水耕栽培キット（アプリ連動）",
        "専用アプリで操作するスマートロック 本体",
        "アプリ対応 充電式 ランプ",
        "アプリ操作できるステンレス製ボトル",
        "앱 연동 스마트 조명 본체",
        "전용 앱으로 제어하는 충전식 청소기",
    ]
    for t in cases:
        check(f"物理商品と判定: {t[:34]}", not g.is_non_physical(t.lower()))


def test_app_only_is_non_physical():
    """アプリ/SaaS のみの企画は従来どおり非物理と判定すること。"""
    print("test_app_only_is_non_physical")
    cases = [
        ("アプリのみのサブスクリプション", "サブスクリプション"),
        ("瞑想アプリを開発します", "アプリ単独"),
        ("новый モバイルアプリ mobile app for meditation", "mobile app"),
        ("가계부 앱 서비스", "앱単独"),
    ]
    for t, why in cases:
        check(f"非物理と判定: {why}", g.is_non_physical(t.lower()))


def test_app_moved_to_weak():
    """「アプリ」が STRONG から WEAK へ移動していること。"""
    print("test_app_moved_to_weak")
    check("'アプリ' は STRONG に無い", "アプリ" not in g._NON_PHYSICAL_STRONG)
    check("'アプリ' は WEAK にある", "アプリ" in g._NON_PHYSICAL_WEAK)
    check("'ソフトウェア' は STRONG のまま", "ソフトウェア" in g._NON_PHYSICAL_STRONG)
    check("'サブスクリプション' は STRONG のまま",
          "サブスクリプション" in g._NON_PHYSICAL_STRONG)
    check("'アプリ連動' は物理指標にある", "アプリ連動" in g._PHYSICAL_PRODUCT_HINTS)


def test_excluded_categories_japanese_integration():
    """_excluded_categories 経由でも日本語ケースが正しいこと。"""
    print("test_excluded_categories_japanese_integration")
    reasons = g._excluded_categories("スマート水耕栽培キット（アプリ連動）".lower())
    check("水耕栽培キットに非物理理由が付かない",
          not any("物理商品ではない" in r for r in reasons))
    reasons2 = g._excluded_categories("瞑想アプリのサブスクリプション".lower())
    check("アプリ単独には非物理理由が付く",
          any("物理商品ではない" in r for r in reasons2))


def test_backward_compat_constant():
    """既存参照が壊れないこと。"""
    print("test_backward_compat_constant")
    check("_NON_PHYSICAL_HINTS が存在する", hasattr(g, "_NON_PHYSICAL_HINTS"))
    check("STRONG と WEAK の合成である",
          set(g._NON_PHYSICAL_HINTS) == set(g._NON_PHYSICAL_STRONG) | set(g._NON_PHYSICAL_WEAK))


def main():
    test_word_boundary()
    test_physical_product_with_companion_app()
    test_true_non_physical_still_detected()
    test_weak_hint_without_physical_context()
    test_excluded_categories_integration()
    test_japanese_companion_app_is_physical()
    test_app_only_is_non_physical()
    test_app_moved_to_weak()
    test_excluded_categories_japanese_integration()
    test_backward_compat_constant()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
