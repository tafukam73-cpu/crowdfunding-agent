"""人手検証済み ground truth（schema B）を JSON に書き出す（authoring スクリプト）。

重要な原則:
  - ここに書くのは **人手で実地確認した事実のみ**（出典: gold_set_v1.py GOLD_B、
    2026-07-18 に WebFetch で公式ページを literally 確認した記録）。
  - **prediction（saved_*）から expected を生成しない**。この関数は saved_* を一切読まない。
  - 証拠が不足するものは verified にせず partially_verified / unresolved にする。
  - auto-label（gold_partial の build-partial 由来）は ground truth に昇格させない。

schema B（1 案件 = 1 dict）:
  project_id, source_site, maker_name,
  expected_official_site (str|None), expected_direct_emails, expected_fallback_emails,
  expected_forms, expected_socials, expected_people ([ [name, role], ... ]),
  plausible_unconfirmed_emails (採点しない: ドメイン一致だがページ未掲載),
  expected_no_public_contact (bool: 公開連絡手段が一切ない),
  verification_status (verified|partially_verified|blocked|unresolved|no_public_contact),
  blocked_reason (str|None), evidence_urls, evidence_snippets,
  verified_at, verified_by

実行: docker exec cfagent-backend python tests/contact_intel_eval/build_ground_truth.py
"""
from __future__ import annotations

import json
from pathlib import Path

VERIFIED_AT = "2026-07-18"
VERIFIED_BY = "manual WebFetch review (gold_set_v1 GOLD_B)"


def _c(pid, site, maker, *, official=None, direct=None, fallback=None, forms=None,
       socials=None, people=None, plausible=None, no_public=False,
       status="verified", blocked_reason=None, evidence=None, snippets=None):
    return dict(
        project_id=pid, source_site=site, maker_name=maker,
        expected_official_site=official,
        expected_direct_emails=direct or [],
        expected_fallback_emails=fallback or [],
        expected_forms=forms or [],
        expected_socials=socials or [],
        expected_people=people or [],
        plausible_unconfirmed_emails=plausible or [],
        expected_no_public_contact=no_public,
        verification_status=status,
        blocked_reason=blocked_reason,
        evidence_urls=evidence or [],
        evidence_snippets=snippets or [],
        verified_at=VERIFIED_AT, verified_by=VERIFIED_BY,
    )


# 24 案件（人手検証リテラル）。数値を良く見せる目的の改変はしない。
GROUND_TRUTH = [
    # --- direct maker email 確認済み（verified）---
    _c(91, "kickstarter", "Schweizer Comics", official="https://schweizercomics.com",
       direct=["chris@curiousoldlibrary.com"], forms=["https://schweizercomics.com/contact"],
       people=[["Chris Schweizer", "Creator (Schweizer Comics)"]],
       evidence=["https://schweizercomics.com/contact"],
       snippets=["公式 contact に chris@curiousoldlibrary.com と本人 Chris Schweizer 掲載"]),
    _c(98, "wadiz", "NOCFREE", official="https://nocfree.kr",
       direct=["help.kr@nocfree.com"],
       socials=["https://www.instagram.com/nocfree.kr/", "https://blog.naver.com/danbin_",
                "https://facebook.com/nocfree"],
       people=[["ZOU WENXIAO", "대표(代表)"]],
       evidence=["https://nocfree.kr"],
       snippets=["公式に help.kr@nocfree.com・代表 ZOU WENXIAO 掲載"]),
    # GT補正(2026-07-23): 当初 p96 は「真メーカー未特定」で unresolved としていたが、
    # 놀로(Knollo) は 스파크펫(SparkPet, sparkpetkorea.com) のブランドであり、
    # Knollo Store(knollo.store) / Knollo Square(knollo.co.kr) / Knollo Play(アプリ) の
    # 3 事業を持つことを確認したため verified へ昇格（公式サイトのみ。メールは据え置き）。
    # 実測(2026-07-23 ライブ疎通):
    #   https://www.knollo.store  200 / TLSv1.3 / CN=www.knollo.store(〜2026-09-24)
    #       <title>놀로 knollo | 반려동물 간식·용품·케어 전문몰</title>
    #       canonical=https://www.knollo.store/  server=Vercel  bot protection なし
    #   https://www.knollo.co.kr  200 / <title>놀로스퀘어</title>（Knollo Square＝実店舗）
    #       ページ内から https://www.knollo.store へリンク（同一ブランドの相互参照）
    # 傍証: SparkLabs ポートフォリオ "Sparkpet (KNOLLO)"、Google Play
    #       com.sparkpet.knollo、platum.kr/archives/199247（스파크펫の놀로アプリ出시）。
    # 代理店判定は維持: hi@/makerlive@brand-kr.com は maker 直通でなく fallback(agency)。
    # CX@sparkpetkorea.com は knollo.store 上に実在するが運営会社ドメインで maker 公式
    # ドメインと不一致のため direct には昇格させず plausible に留める。
    _c(96, "wadiz", "놀로 (Knollo / 스파크펫)", official="https://www.knollo.store",
       fallback=["hi@brand-kr.com", "makerlive@brand-kr.com"],
       plausible=["CX@sparkpetkorea.com"],
       evidence=["https://www.knollo.store", "https://www.knollo.co.kr",
                 "https://sparklabs.co.kr/kr/portfolio/sparkpet-knollo/"],
       snippets=["公式 knollo.store を確認(<title>놀로 knollo | 반려동물 간식·용품·케어 전문몰"
                 "・canonical=https://www.knollo.store/)。",
                 "놀로=스파크펫(SparkPet)のブランド。Store/Square/Play の3事業。",
                 "brand-kr.com は運営代行(代理店)で maker 直通でなく fallback のまま。",
                 "CX@sparkpetkorea.com は運営会社ドメインのため未確認(plausible)扱い"]),
    # GT補正(2026-07-23): 当初 p97 は「真メーカー未特定」で unresolved としていたが、
    # 주부디자인 は **호정아이앤티(I&T)** の消費者向け流通ブランドで、公式ショップが
    # jubudesign.com であることを事業者情報レベルで確認したため verified へ昇格。
    # 自社サイトの説明文（逐語）:
    #   「주부디자인은 30년 이상을 스테인리스 와이어와 정사각형 압연 소재의 수납용품을
    #     전문적으로 제조하는 '호정I&T'의 소비자 유통 브랜드입니다.」
    # 実測(2026-07-23): https://jubudesign.com/ 200 / UTF-8 /
    #   <title>주부디자인</title> / og:site_name=주부디자인 / canonical=https://jubudesign.com/
    #   事業者情報（全ページ共通・HTML 逐語）:
    #     상호=호정아이앤티(I&T) / 대표자=이순향 외 1명 / 사업자번호=212-22-58903
    #     통신판매업=제 2016-인천부평-0766 호 / judy@jubudesign.com
    #     주소=21315 인천광역시 부평구 새벌로 44 통일개발1층
    # 傍証: 네이버 블로그「와디즈 펀딩에서 최초 출시! 주부디자인 큐브EGI물받침싱크랙」
    #       （큐브 시리즈は jubudesign.com に実在）、Instagram @jubudesign_official、
    #       SSG(신세계몰) ブランドページ。
    # 公式 URL は **jubudesign.com**（cafe24 サブドメインではない）:
    #   jubudesign.cafe24.com は同一実体だが canonical/og:url が jubudesign.com を指す。
    #   加えて so.registrable_domain("jubudesign.cafe24.com") は共有ホスティングの
    #   "cafe24.com" に潰れるため、公式に据えると judy@jubudesign.com が third_party に
    #   落ちる（実測）。ブランドショップを採用する方針は p96 놀로→knollo.store と同型。
    # 代理店判定は維持: real1@makerz.co.kr は maker 直通でなく fallback(agency)。
    _c(97, "wadiz", "주부디자인 (호정아이앤티 I&T)", official="https://jubudesign.com",
       fallback=["real1@makerz.co.kr"],
       plausible=["judy@jubudesign.com"],
       evidence=["https://jubudesign.com", "https://jubudesign.com/shopinfo/company.html"],
       snippets=["公式 jubudesign.com を確認(<title>주부디자인・canonical=https://jubudesign.com/)。",
                 "주부디자인은 …'호정I&T'의 소비자 유통 브랜드입니다(自社サイト逐語)。",
                 "사업자번호 212-22-58903 / 대표자 이순향 외 1명 / 통신판매업 제 2016-인천부평-0766 호。",
                 "makerz.co.kr は運営代行(代理店)で maker 直通でなく fallback のまま。",
                 "judy@jubudesign.com は掲載確認済みだが人手での役割未確認のため plausible"]),
    _c(108, "zeczec", "Single Step", official="https://singlestep.com",
       direct=["info@singlestep.com"],
       plausible=[],  # mediafol.io / ulpi.com.tw は第三者（expected に入れない）
       evidence=["https://singlestep.com"],
       snippets=["公式に info@singlestep.com 掲載。mediafol.io/ulpi.com.tw は第三者で不採用"]),
    _c(117, "kickstarter", "Sharge", official="https://sharge.com",
       direct=["info@sharge.com"], forms=["https://sharge.com/pages/contact-us"],
       socials=["https://www.facebook.com/sharge.fans", "https://twitter.com/sharge_official",
                "https://www.instagram.com/sharge_official/",
                "https://www.youtube.com/@sharge_official",
                "https://www.tiktok.com/@sharge_official"],
       evidence=["https://sharge.com/pages/contact-us"],
       snippets=["公式 contact に info@sharge.com 掲載"]),
    _c(128, "indiegogo", "Arcwave (WOW Tech)", official="https://www.arcwave.com",
       direct=["care@arcwave.com"],
       forms=["https://www.arcwave.com/us/contact", "https://wowtech.com/wholesale/"],
       socials=["https://www.instagram.com/arcwave.official/"],
       evidence=["https://www.arcwave.com/us/contact", "https://wowtech.com/wholesale/"],
       snippets=["公式 contact に care@arcwave.com・卸フォーム wowtech/wholesale 確認"]),
    _c(136, "wadiz", "주식회사 더넥스트 (nextbaby)", official="https://nextbaby.co.kr",
       direct=["momtobabydj@naver.com"],
       plausible=["cs@nextbaby.co.kr", "info@nextbaby.co.kr", "help@nextbaby.co.kr"],
       socials=["https://www.instagram.com/hybrid__korea/", "https://pf.kakao.com/_TBjln"],
       people=[["김미정", "CEO(대표)"]],
       evidence=["https://nextbaby.co.kr"],
       snippets=["법인 주식회사 더넥스트・CEO 김미정・momtobabydj@naver.com 確認。"
                 "@nextbaby.co.kr 系はドメイン一致だが未掲載=採点対象外"]),

    # --- verified だが公開メールなし（form/SNS/people はあり）---
    _c(104, "zeczec", "優程工業 (Union Chen)", official="https://shop.unionchen.com.tw",
       forms=["https://shop.unionchen.com.tw/contact.php?type=2"],
       socials=["https://www.facebook.com/chiaofu.goldenfox",
                "https://www.youtube.com/c/UnionChen",
                "https://www.instagram.com/chiaofu_goldenfox/", "https://lin.ee/Zl3QDUd"],
       plausible=["chiaofu@unionchen.com.tw"],
       evidence=["https://shop.unionchen.com.tw"],
       snippets=["公式にメール掲載なし・電話/LINE/フォーム。chiaofu@unionchen.com.tw は"
                 "ドメイン一致だが未掲載=採点対象外"]),
    _c(107, "zeczec", "裡外生活 (Leewayworld)", official="https://www.leewayworld.com",
       forms=["https://www.leewayworld.com/contact"],
       socials=["https://page.line.me/645rrcab", "https://m.me/107046535517915",
                "https://www.youtube.com/@TWLeewayworld"],
       plausible=["hello@leewayworld.com"],
       evidence=["https://www.leewayworld.com/contact"],
       snippets=["公式にメール掲載なし・フォーム/LINE/Messenger。hello@leewayworld.com は未掲載=採点対象外"]),
    # GT補正(2026-07-18): 当初 GOLD_B は「公式にメール掲載なし」としていたが、深掘りクロール
    # ＋GT監査で maker 公式ドメイン hanboost.com 上に営業メール3件が実在することを HTML 証拠で
    # 確認したため expected_direct_emails へ昇格（人手検証・第三者ではなく maker_official）。
    # 証拠:
    #   sales@hanboost.com    @ https://www.hanboost.com/pages/contact
    #       "<strong>Business Collboration</strong>:sales@hanboost.com"
    #   support@hanboost.com  @ https://www.hanboost.com/pages/contact
    #       "<strong>General Inquiry: </strong>support@hanboost.com"
    #   marketing@hanboost.com @ https://www.hanboost.com/
    #       "feel free to contact marketing@hanboost.com , or submit your Maker..."
    _c(118, "indiegogo", "Hanboost", official="https://www.hanboost.com",
       direct=["sales@hanboost.com", "support@hanboost.com", "marketing@hanboost.com"],
       forms=["https://www.hanboost.com/pages/contact"],
       socials=["https://www.facebook.com/hanboost", "https://instagram.com/hanboostshop/",
                "https://linkedin.com/company/hanboost", "https://tiktok.com/@hanboostshop"],
       evidence=["https://www.hanboost.com/pages/contact", "https://www.hanboost.com/"],
       snippets=["公式 hanboost.com を確認(T1 Laser Engraver＋KS リンク一致)。",
                 "GT補正: /pages/contact に 'Business Collboration: sales@hanboost.com' / "
                 "'General Inquiry: support@hanboost.com'、ホームに 'contact marketing@hanboost.com' "
                 "を掲載(平文)。maker_official のため direct へ昇格。",
                 "kickbooster.me は販促サービスで maker フォームではない"]),
    _c(8, "kickstarter", "alltimelab", official="https://alltimelab.com",
       socials=["https://www.instagram.com/alltime_universe/",
                "https://www.youtube.com/@alltime_universe"],
       people=[["Kumi", "Brand designer"], ["Amie", "Marketing specialist"]],
       evidence=["https://alltimelab.com"],
       snippets=["公式にチーム(Kumi=Brand designer 等)掲載・メールなし"]),
    _c(135, "kickstarter", "SOLÈNE", official="https://officielsolene.com",
       forms=["https://officielsolene.com/pages/contact"],
       socials=["https://tiktok.com/@officielsolene", "https://instagram.com/officielsolene"],
       evidence=["https://officielsolene.com/pages/contact"],
       snippets=["公式はフォーム/SNS のみ(メール掲載なし)。solene.jp/solene-musique.com は別ブランドの疑い"]),

    # --- verified: 公開連絡手段が実質なし / maker でない ---
    _c(9, "kickstarter", "AVIX Lab", official="https://avixlab.com", no_public=True,
       evidence=["https://avixlab.com"],
       snippets=["公式は Coming Soon で連絡先なし。実チャネルなし"]),
    _c(111, "zeczec", "親子天下Shopping（小売・非メーカー）", no_public=True,
       status="verified", blocked_reason="not_a_maker: 親子天下は小売/プラットフォームでメーカーでない",
       evidence=["https://shopping.parenting.com.tw"],
       snippets=["親子天下Shopping＝小売でメーカーでない。support@zeczec.com=運営メール・"
                 "parenting@cw.com.tw=小売メールでいずれも maker 直通でない"]),
    _c(12, "kickstarter", "andrei jay（個人アーティスト）", no_public=True,
       evidence=[], snippets=["個人アーティストで法人サイトなし。公開連絡手段なし"]),

    # --- unresolved: 真メーカー未特定（precision/recall の分母から除外）---
    _c(110, "kickstarter", "MoodMate maker (未特定)", status="unresolved",
       blocked_reason="true maker not identified; lg.com/lge.com は無関係大企業(別法人)",
       evidence=[], snippets=["System 公式=lg.com(無関係大企業)・メール=lge.com(別法人)は完全誤認。"
                              "真メーカー未特定"]),
    _c(19, "kickstarter", "StarDome Pod maker (未特定)", status="unresolved",
       fallback=["apply@ideafound.com"],
       blocked_reason="true maker not identified; stardome.com は別法人(comedy)・ideafound.com は代理店",
       evidence=[], snippets=["stardome.com は comedy/別法人。ideafound.com は代理店(maker 直通でない)。"
                              "真メーカー未特定(stardome.com/contact 403)"]),
    _c(26, "indiegogo", "Quieto maker (未特定)", status="unresolved",
       fallback=["apply@ideafound.com"],
       blocked_reason="true maker not identified; ideafound.com は代理店",
       evidence=[], snippets=["email=ideafound.com(代理店)。真メーカー未特定"]),

    # --- blocked: サイト到達不能/接続不安定で未確認 ---
    _c(116, "zeczec", "Roly One / Suntrail", official="https://suntrail.com.tw",
       status="blocked", blocked_reason="official site unreachable (socket closed)",
       plausible=["cs.suntrail@gmail.com"],
       evidence=["https://suntrail.com.tw"],
       snippets=["公式サイト接続不可(socket closed)。cs.suntrail@gmail.com は未確認。"
                 "zeczec.com/contact は運営フォーム=FP"]),
    _c(115, "zeczec", "AGGvol1", status="blocked",
       blocked_reason="no real channel found; zeczec 運営 form/social のみ",
       evidence=[], snippets=["System は zeczec 運営 form/social のみ＝実チャネルなし"]),
    _c(122, "indiegogo", "HercLéon", official="https://hercleon.com", status="blocked",
       blocked_reason="official site unstable/404 (connection)",
       socials=["https://www.instagram.com/hercleon/", "https://www.facebook.com/Hercleon"],
       evidence=["https://hercleon.com"],
       snippets=["hercleon.com は接続不安定/404 で連絡先未確認。m.me/contact は Messenger 誤採用"]),

    # --- partially_verified: 一部確認・一部未確認（gmail 未確定）---
    _c(109, "zeczec", "綠色沙河工作室 (Green River Studio)", status="partially_verified",
       plausible=["greenriverstudio7777@gmail.com"],
       blocked_reason="maker gmail plausible but unconfirmed; admin@reurl.cc は短縮URL運営者(非maker)",
       evidence=[], snippets=["gmail のみ maker らしい(未確定)。admin@reurl.cc は短縮URL(reurl.cc)"
                              "運営者メールで maker でない=不採用"]),
    _c(105, "zeczec", "MORESIE", official="https://moresie.com", status="partially_verified",
       plausible=["moreshop07@gmail.com"],
       blocked_reason="official JS-heavy; gmail candidates unconfirmed",
       evidence=["https://moresie.com"],
       snippets=["公式は JS で本文薄く連絡先取得不可。gmail 2 件は未確認=採点対象外"]),
]


def main() -> int:
    ids = [c["project_id"] for c in GROUND_TRUTH]
    assert len(ids) == len(set(ids)), "duplicate project_id"
    out = Path(__file__).resolve().parent / "gold_ground_truth.json"
    out.write_text(json.dumps(GROUND_TRUTH, ensure_ascii=False, indent=2), encoding="utf-8")
    by_status: dict[str, int] = {}
    for c in GROUND_TRUTH:
        by_status[c["verification_status"]] = by_status.get(c["verification_status"], 0) + 1
    print(f"ground truth 書き出し: {len(GROUND_TRUTH)} 案件 -> {out.name}")
    print(f"  verification_status 内訳: {by_status}")
    direct = sum(len(c["expected_direct_emails"]) for c in GROUND_TRUTH)
    fb = sum(len(c["expected_fallback_emails"]) for c in GROUND_TRUTH)
    print(f"  direct email 総数={direct}  fallback email 総数={fb}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
