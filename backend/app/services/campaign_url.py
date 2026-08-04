"""海外クラファン商品ページ URL（campaign_url）の解決・検証。

**正規フィールドは `projects.source_url`**（収集時に必ず案件ページを入れる一意キー）。
新しい重複カラムは追加せず、このモジュールが「source_site と整合する案件ページ URL か」
を判定して `campaign_url` として公開する。メーカー公式サイト（`projects.maker_url`）は
`official_site_url` として明確に分離し、campaign_url の代用にはしない。

対応サイト: Kickstarter / Indiegogo / Wadiz / Zeczec。
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.models.project import SourceSite
from app.services.source_ownership import (
    CROWDFUNDING_MARKETING,
    CROWDFUNDING_PLATFORMS,
)
from app.services.url_validation import is_valid_business_url

# source_site → 商品ページとして認める host のサフィックス。
# 「その sitesのドメイン配下であること」だけを見る（パス形式はサイト改修で変わるため）。
PLATFORM_DOMAINS: dict[str, tuple[str, ...]] = {
    SourceSite.kickstarter.value: ("kickstarter.com",),
    SourceSite.indiegogo.value: ("indiegogo.com",),
    SourceSite.wadiz.value: ("wadiz.kr",),
    SourceSite.zeczec.value: ("zeczec.com",),
}

# campaign_url が無い / 使えない理由（状態として保存・表示・ログに残す）。
REASON_NO_URL = "no_source_url"                 # そもそも URL が無い
REASON_INVALID_URL = "invalid_url"              # ダミー/プレースホルダー等で URL として無効
REASON_NOT_CAMPAIGN_DOMAIN = "not_campaign_domain"  # source_site のドメインと不一致
REASON_UNSUPPORTED_SITE = "unsupported_site"    # 対応 4 サイト以外（makuake 等）


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def host_matches(url: str | None, site: str | None) -> bool:
    """URL のホストが source_site のクラファンドメイン配下かを返す。"""
    if not url or not site:
        return False
    domains = PLATFORM_DOMAINS.get(str(site))
    if not domains:
        return False
    host = _host(url)
    return any(host == d or host.endswith("." + d) for d in domains)


def classify(url: str | None, site: str | None) -> str | None:
    """campaign_url として使えない理由を返す（使えるなら None）。"""
    if str(site) not in PLATFORM_DOMAINS:
        return REASON_UNSUPPORTED_SITE
    if not url or not str(url).strip():
        return REASON_NO_URL
    if not is_valid_business_url(url):
        return REASON_INVALID_URL
    if not host_matches(url, site):
        return REASON_NOT_CAMPAIGN_DOMAIN
    return None


def campaign_url_of(project) -> str | None:
    """案件の商品ページ URL を返す（source_site と整合しない場合は None）。

    **maker_url（公式サイト）へフォールバックしない**。取得できない場合は None を返し、
    呼び出し側が「商品ページURL未確認」として扱う。
    """
    url = getattr(project, "source_url", None)
    if classify(url, getattr(project, "source_site", None)) is not None:
        return None
    return str(url).strip()


def is_platform_host(url: str | None) -> bool:
    """URL がクラファンプラットフォーム／支援代行のドメイン配下かを返す。

    `maker_url` には Kickstarter の `/profile/<slug>` のような **プラットフォーム内の
    プロフィールページ**が入ることが多い。これは公式サイトではないため、公式サイト
    判定から除外する必要がある。
    """
    if not url:
        return False
    host = _host(url)
    if not host:
        return False
    return any(
        host == d or host.endswith("." + d)
        for d in (*CROWDFUNDING_PLATFORMS, *CROWDFUNDING_MARKETING)
    )


def official_site_url_of(project) -> str | None:
    """メーカー/商品の公式サイト URL を返す（案件ページとは別物）。

    `maker_url` がクラファンプラットフォーム配下（例: kickstarter.com/profile/xxx）の
    場合は **公式サイトではない**ため None を返す。公式サイトが必要な呼び出し側は
    `official_site_verifier.verify_candidate()` で別途確定すること。
    """
    url = getattr(project, "maker_url", None)
    if not url or not is_valid_business_url(url):
        return None
    if is_platform_host(url):
        return None
    return str(url).strip()


def missing_reason(project) -> str | None:
    """campaign_url を取得できない理由（取得できるなら None）。"""
    return classify(
        getattr(project, "source_url", None), getattr(project, "source_site", None)
    )


def url_state(project) -> dict:
    """API / UI が共通で使う URL 状態を返す。"""
    campaign = campaign_url_of(project)
    return {
        "campaign_url": campaign,
        "campaign_url_missing": campaign is None,
        "campaign_url_missing_reason": missing_reason(project),
        "official_site_url": official_site_url_of(project),
    }
