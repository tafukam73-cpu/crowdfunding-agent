"""campaign_url.official_site_url_of がプラットフォーム内プロフィールを除外することの検証。

`maker_url` には Kickstarter の /profile/<slug> のようなプラットフォーム内ページが
入ることが多い。これを official_site_url として返すと、以降のメール所有者判定
（公式ドメイン一致）が全て壊れる。gold 案件をハードコードせず、一般ルールを検証する。

実行: docker compose exec -T backend python tests/test_official_site_url_platform.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / 'official_site_url.sqlite'}"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services import campaign_url as cu  # noqa: E402

_passed = _failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok  - {name}")
    else:
        _failed += 1
        print(f"  FAIL- {name}")


class FakeProject:
    def __init__(self, maker_url=None, source_url=None, source_site="kickstarter"):
        self.maker_url = maker_url
        self.source_url = source_url
        self.source_site = source_site


def test_platform_profile_is_not_official_site():
    print("test_platform_profile_is_not_official_site")
    cases = [
        ("https://www.kickstarter.com/profile/relod", "kickstarter プロフィール"),
        ("https://kickstarter.com/profile/someone", "kickstarter（www なし）"),
        ("https://www.indiegogo.com/individuals/12345", "indiegogo 個人ページ"),
        ("https://www.wadiz.kr/web/wmakerprofile/12345", "wadiz メイカーページ"),
        ("https://www.zeczec.com/users/abc", "zeczec ユーザーページ"),
        ("https://www.makuake.com/project/xxx/", "makuake"),
        ("https://camp-fire.jp/profile/xxx", "CAMPFIRE"),
        ("https://greenfunding.jp/lab/projects/xxx", "GREENFUNDING"),
    ]
    for url, label in cases:
        check(f"{label} は official_site_url にしない",
              cu.official_site_url_of(FakeProject(maker_url=url)) is None)


def test_marketing_and_pledge_manager_excluded():
    print("test_marketing_and_pledge_manager_excluded")
    for url, label in [
        ("https://www.backerkit.com/projects/relod/xxx", "BackerKit"),
        ("https://kickbooster.me/xxx", "Kickbooster"),
        ("https://crowdox.com/xxx", "CrowdOx"),
    ]:
        check(f"{label} は official_site_url にしない",
              cu.official_site_url_of(FakeProject(maker_url=url)) is None)


def test_real_official_site_is_kept():
    print("test_real_official_site_is_kept")
    # 注: is_valid_business_url が "example" 等のダミードメインを弾くため、
    #     実在しうるドメイン形式で検証する。
    for url in ("https://relodin.com/", "https://aurora-devices.io",
                "https://sharge.co.jp/company", "https://shop.brand.com"):
        check(f"{url} は公式サイトとして残る",
              cu.official_site_url_of(FakeProject(maker_url=url)) == url.strip())


def test_invalid_and_missing():
    print("test_invalid_and_missing")
    check("maker_url が None なら None",
          cu.official_site_url_of(FakeProject(maker_url=None)) is None)
    check("空文字なら None",
          cu.official_site_url_of(FakeProject(maker_url="")) is None)


def test_is_platform_host():
    print("test_is_platform_host")
    check("kickstarter.com は platform", cu.is_platform_host("https://www.kickstarter.com/profile/x"))
    check("サブドメインも platform", cu.is_platform_host("https://sub.kickstarter.com/x"))
    check("relodin.com は platform でない", not cu.is_platform_host("https://relodin.com/"))
    check("None は False", not cu.is_platform_host(None))
    # 部分文字列で誤爆しないこと（"notkickstarter.com" を platform 扱いしない）
    check("notkickstarter.com は platform でない",
          not cu.is_platform_host("https://notkickstarter.com/"))


def test_campaign_url_unaffected():
    """campaign_url 側の挙動は変えていないこと（回帰防止）。"""
    print("test_campaign_url_unaffected")
    p = FakeProject(
        maker_url="https://www.kickstarter.com/profile/relod",
        source_url="https://www.kickstarter.com/projects/relod/ovo-air-2",
        source_site="kickstarter",
    )
    check("campaign_url は従来どおり取得できる",
          cu.campaign_url_of(p) == "https://www.kickstarter.com/projects/relod/ovo-air-2")
    st = cu.url_state(p)
    check("url_state の campaign_url は維持", st["campaign_url"] is not None)
    check("url_state の official_site_url は None になる", st["official_site_url"] is None)
    check("url_state のキー構成は不変",
          set(st) == {"campaign_url", "campaign_url_missing",
                      "campaign_url_missing_reason", "official_site_url"})


def main():
    test_platform_profile_is_not_official_site()
    test_marketing_and_pledge_manager_excluded()
    test_real_official_site_is_kept()
    test_invalid_and_missing()
    test_is_platform_host()
    test_campaign_url_unaffected()
    print(f"\n{_passed} passed / {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
