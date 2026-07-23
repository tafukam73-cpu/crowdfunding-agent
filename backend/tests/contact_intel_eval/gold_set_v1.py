"""改善前ベースライン測定：Contact Intelligence(A) vs 通常 Claude 調査(B) の比較。

gold set は「実 DB の 24 実在案件（フィクスチャ除外・maker 重複排除）」。System A は
DB 保存済みの現行パイプライン出力（gold_candidates.json）。Claude B は 2026-07-18 に
WebFetch で **公式ページを実地取得して literally 確認**した公開連絡先（推測を入れない。
確認できなかった/bot ブロックは blocked とし成果にしない）。

この JSON 化した判定を機械集計して 10 指標を出す。数値の分母・分子・根拠を明示する。
実行: docker exec cfagent-backend python tests/contact_intel_eval/gold_set_v1.py
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAND = HERE / "gold_candidates.json"

# --- Claude B：2026-07-18 に WebFetch で literally 確認した正解（推測なし）---
# status: verified=公式ページで実確認 / partial=一部確認・一部到達不能 / blocked=bot/接続で未確認
# b_emails: 公式ページに掲載されていた実メール（その場で根拠確認できた公開情報のみ）
# b_official: Claude が根拠付きで確定した公式サイト（campaign 本文/製品名/KSリンク一致等）
# a_email_class: System A の saved_emails を分類（correct=公式ドメイン一致/掲載確認,
#                platform=クラファン運営, agency=代理店/第三者, unrelated=無関係大企業,
#                junk=短縮URL/破損/messenger, gmail_unverified=gmail で未確認）
GOLD_B: dict[int, dict] = {
    110: dict(status="verified", b_official=None, b_emails=[], b_forms=[], b_socials=[],
              b_people=[], note="製品 MoodMate。System は公式=lg.com（無関係大企業）・"
              "メール=LG/lge.com（別法人）と完全誤認。Claude も真のメーカー未特定。",
              a_email_class="unrelated", a_official_correct=False),
    109: dict(status="partial", b_official=None,
              b_emails=["greenriverstudio7777@gmail.com"], b_forms=[], b_socials=[],
              b_people=[], note="綠色沙河工作室。System に admin@reurl.cc(短縮URL)・"
              "破損メール・他ブランド混入。gmail のみ maker らしい。",
              a_email_class="junk", a_official_correct=False),
    104: dict(status="verified", b_official="https://shop.unionchen.com.tw",
              b_emails=[], b_forms=["https://shop.unionchen.com.tw/contact.php?type=2"],
              b_socials=["https://www.facebook.com/chiaofu.goldenfox",
                         "https://www.youtube.com/c/UnionChen",
                         "https://www.instagram.com/chiaofu_goldenfox/",
                         "https://lin.ee/Zl3QDUd"],
              b_people=[], note="優程工業(Union Chen)。公式にメール掲載なし・電話/LINE/フォーム。"
              "System の chiaofu@unionchen.com.tw はドメイン一致で妥当。System socials に "
              "x.com/zeczec_com(運営)混入・LINE 取りこぼし。",
              a_email_class="correct", a_official_correct=True),
    116: dict(status="blocked", b_official="https://suntrail.com.tw",
              b_emails=[], b_forms=[], b_socials=[], b_people=[],
              note="Roly One / Suntrail。公式サイト接続不可(socket closed)。System forms は "
              "zeczec.com/contact(運営)＝FP。email cs.suntrail@gmail.com は未確認。",
              a_email_class="gmail_unverified", a_official_correct=True),
    108: dict(status="verified", b_official="https://singlestep.com",
              b_emails=["info@singlestep.com"], b_forms=[], b_socials=[], b_people=[],
              note="Single Step。info@singlestep.com 確認。System は info@singlestep.com 正・"
              "singlestep@ulpi.com.tw/info@mediafol.io は第三者。socials に zeczec(運営)混入。",
              a_email_class="correct", a_official_correct=True),
    115: dict(status="blocked", b_official=None, b_emails=[], b_forms=[], b_socials=[],
              b_people=[], note="AGGvol1。System は zeczec 運営 form/social のみ＝実チャネルなし。",
              a_email_class="none", a_official_correct=False),
    105: dict(status="partial", b_official="https://moresie.com",
              b_emails=[], b_forms=[], b_socials=[], b_people=[],
              note="MORESIE。公式は JS で本文薄く連絡先取得不可。System の gmail 2 件は未確認。",
              a_email_class="gmail_unverified", a_official_correct=True),
    107: dict(status="verified", b_official="https://www.leewayworld.com",
              b_emails=[], b_forms=["https://www.leewayworld.com/contact"],
              b_socials=["https://page.line.me/645rrcab",
                         "https://m.me/107046535517915",
                         "https://www.youtube.com/@TWLeewayworld"],
              b_people=[], note="裡外生活(Leewayworld)。公式にメール掲載なし・フォーム/LINE/"
              "Messenger。System の hello@leewayworld.com は公式ページ未掲載(要確認)。"
              "System socials に zeczec(運営)混入・LINE/Messenger 取りこぼし。",
              a_email_class="gmail_unverified", a_official_correct=True),
    111: dict(status="verified", b_official=None, b_emails=[], b_forms=[], b_socials=[],
              b_people=[], note="親子天下Shopping＝小売/プラットフォームでメーカーでない。"
              "System に support@zeczec.com(運営メール誤採用)＋parenting@cw.com.tw(小売)。",
              a_email_class="platform", a_official_correct=False),
    91: dict(status="verified", b_official="https://schweizercomics.com",
             b_emails=["chris@curiousoldlibrary.com"], b_forms=["https://schweizercomics.com/contact"],
             b_socials=[], b_people=[["Chris Schweizer", "Creator (Schweizer Comics)"]],
             note="chris@curiousoldlibrary.com＋本人 Chris Schweizer 確認。System はメール/公式正だが "
             "socials=kickstarter 自身(FP)・people=KS UI 断片(FP)で実在人物を取りこぼし。",
             a_email_class="correct", a_official_correct=True),
    117: dict(status="verified", b_official="https://sharge.com",
              b_emails=["info@sharge.com"], b_forms=["https://sharge.com/pages/contact-us"],
              b_socials=["https://www.facebook.com/sharge.fans",
                         "https://twitter.com/sharge_official",
                         "https://www.instagram.com/sharge_official/",
                         "https://www.youtube.com/@sharge_official",
                         "https://www.tiktok.com/@sharge_official"],
              b_people=[], note="info@sharge.com 確認。System はメール/公式正だが socials に "
              "facebook.com/kickstarter(FP)混入・tiktok/discord/正 FB 取りこぼし。",
              a_email_class="correct", a_official_correct=True),
    9: dict(status="verified", b_official="https://avixlab.com", b_emails=[], b_forms=[],
            b_socials=[], b_people=[], note="AVIX Lab。公式は Coming Soon で連絡先なし。"
            "System socials=kickstarter 自身(FP)・people=KS UI 断片(FP)。実チャネルなし。",
            a_email_class="none", a_official_correct=True),
    8: dict(status="verified", b_official="https://alltimelab.com", b_emails=[], b_forms=[],
            b_socials=["https://www.instagram.com/alltime_universe/",
                       "https://www.youtube.com/@alltime_universe"],
            b_people=[["Kumi", "Brand designer"], ["Amie", "Marketing specialist"]],
            note="alltimelab。公式にチーム(Kumi=Brand designer 等)掲載・メールなし。System は "
            "people=KS UI 断片(FP)で実在チームを取りこぼし。",
            a_email_class="none", a_official_correct=True),
    118: dict(status="verified", b_official="https://www.hanboost.com", b_emails=[],
              b_forms=["https://www.hanboost.com/pages/contact"],
              b_socials=["https://www.facebook.com/hanboost",
                         "https://instagram.com/hanboostshop/",
                         "https://linkedin.com/company/hanboost",
                         "https://tiktok.com/@hanboostshop"],
              b_people=[], note="★公式 hanboost.com を Claude が即特定(T1 Laser Engraver＋KS リンク一致)。"
              "System は公式を取りこぼし・kickbooster.me(販促)を form 誤採用・twitter=KS(FP)。"
              "公式にメール掲載はなし(フォーム/8 SNS)。",
              a_email_class="none", a_official_correct=False),
    122: dict(status="blocked", b_official="https://hercleon.com", b_emails=[], b_forms=[],
              b_socials=["https://www.instagram.com/hercleon/",
                         "https://www.facebook.com/Hercleon"],
              b_people=[], note="HercLéon。hercleon.com は接続不安定/404 で連絡先未確認。"
              "System は m.me/contact(Messenger)を form 誤採用。",
              a_email_class="none", a_official_correct=False),
    12: dict(status="verified", b_official=None, b_emails=[], b_forms=[], b_socials=[],
             b_people=[], note="andrei jay＝個人アーティスト。法人サイトなし。System は "
             "kickstarter 自身の SNS(FP)のみ。実チャネルなし。",
             a_email_class="none", a_official_correct=False),
    19: dict(status="blocked", b_official=None, b_emails=[], b_forms=[], b_socials=[],
             b_people=[], note="StarDome Pod。System 公式=stardome.com は comedy/別法人(FP)・"
             "email=ideafound.com(代理店)。真メーカー未特定(stardome.com/contact 403)。",
             a_email_class="agency", a_official_correct=False),
    26: dict(status="blocked", b_official=None, b_emails=[], b_forms=[], b_socials=[],
             b_people=[], note="Quieto。System email=ideafound.com(代理店)・social=indiegogo 自身(FP)。"
             "真メーカー未特定。",
             a_email_class="agency", a_official_correct=False),
    135: dict(status="verified", b_official="https://officielsolene.com",
              b_emails=[], b_forms=["https://officielsolene.com/pages/contact"],
              b_socials=["https://tiktok.com/@officielsolene",
                         "https://instagram.com/officielsolene"],
              b_people=[], note="SOLÈNE。公式はフォーム/SNS のみ(メール掲載なし)。System の "
              "solne.com は一致だが solene.jp/solene-musique.com は別ブランドの疑い。",
              a_email_class="correct", a_official_correct=True),
    98: dict(status="verified", b_official="https://nocfree.kr",
             b_emails=["help.kr@nocfree.com"], b_forms=[],
             b_socials=["https://www.instagram.com/nocfree.kr/",
                        "https://blog.naver.com/danbin_",
                        "https://facebook.com/nocfree"],
             b_people=[["ZOU WENXIAO", "대표(代表)"]],
             note="help.kr@nocfree.com＋代表 ZOU WENXIAO 確認。System メール/公式/主要 SNS 正・"
             "naver blog/kakao/代表 取りこぼし。",
             a_email_class="correct", a_official_correct=True),
    128: dict(status="verified", b_official="https://www.arcwave.com",
              b_emails=["care@arcwave.com"],
              b_forms=["https://www.arcwave.com/us/contact", "https://wowtech.com/wholesale/"],
              b_socials=["https://www.instagram.com/arcwave.official/"],
              b_people=[], note="★care@arcwave.com＋卸フォーム(wowtech/wholesale)を Claude が確認。"
              "System はメールを取りこぼし(saved_emails 空)・公式/SNS は正。",
              a_email_class="none", a_official_correct=True),
    136: dict(status="verified", b_official="https://nextbaby.co.kr",
              b_emails=["momtobabydj@naver.com"], b_forms=[],
              b_socials=["https://www.instagram.com/hybrid__korea/",
                         "https://pf.kakao.com/_TBjln"],
              b_people=[["김미정", "CEO(대표)"]],
              note="法人 주식회사 더넥스트・CEO 김미정・momtobabydj@naver.com 確認。System は "
              "@nextbaby.co.kr 3 件(ドメイン一致・未確認)＋naver 正だが CEO/kakao 取りこぼし。",
              a_email_class="correct", a_official_correct=True),
    # GT補正(2026-07-23): 当初 blocked（公式未特定）だったが、놀로=스파크펫(SparkPet) の
    # ブランドで Knollo Store(knollo.store)/Knollo Square(knollo.co.kr)/Knollo Play(アプリ)
    # の 3 事業を持つことを確認。knollo.store を実地取得（200・<title>놀로 knollo |
    # 반려동물 간식·용품·케어 전문몰・canonical=https://www.knollo.store/）したため verified へ。
    # System A の saved_official_sites は空だったので a_official_correct=False のまま。
    # brand-kr.com の agency 判定も維持する。
    96: dict(status="verified", b_official="https://www.knollo.store",
             b_emails=[], b_forms=[],
             b_socials=["https://litt.ly/knollo.store"],
             b_people=[], note="놀로(Knollo)=스파크펫(SparkPet)のブランド。公式 knollo.store を"
             "実地確認(놀로 knollo | 반려동물 간식·용품·케어 전문몰)。knollo.co.kr は"
             "Knollo Square(実店舗)で同一ブランド。System email=brand-kr.com は代理店のまま、"
             "公式サイトは未取得だった。",
             a_email_class="agency", a_official_correct=False),
    97: dict(status="blocked", b_official=None, b_emails=[], b_forms=[], b_socials=[],
             b_people=[], note="주부디자인。System email=makerz.co.kr(代理店の疑い)。公式未特定。",
             a_email_class="agency", a_official_correct=False),
}


def _platform_social(url: str) -> bool:
    u = url.lower()
    return any(p in u for p in (
        "/kickstarter", "kickstarter.com/projects", "twitter.com/kickstarter",
        "zeczec_com", "zeczec.com", "/indiegogo", "indiegogo.com/", "facebook.com/Indiegogo",
    ))


def _real_socials(socials: list[str]) -> list[str]:
    return [s for s in socials if not _platform_social(s)]


def main() -> int:
    cand = {c["project_id"]: c for c in json.loads(CAND.read_text(encoding="utf-8"))}
    ids = [i for i in GOLD_B if i in cand]
    n = len(ids)

    # --- 集計 ---
    a_email = a_email_wrong = a_form = a_channel = a_person = 0
    a_official = a_platform_email = a_email_wrong_cases = 0
    b_email = b_form = b_channel = b_person = b_official = 0
    claude_only = system_only = 0
    fail_reasons: dict[str, int] = {}
    per_case = []

    for i in ids:
        c = cand[i]
        g = GOLD_B[i]
        a_emails = c["saved_emails"]
        a_real_socials = _real_socials(c["saved_socials"])
        # System が platform 運営 form/messenger/短縮を除いた「実フォーム」
        a_real_forms = [f for f in c["saved_forms"] if not any(
            x in f.lower() for x in ("zeczec.com/contact", "kickbooster", "m.me/",
                                     "indiegogo.com", "kickstarter.com"))]
        a_has_email = bool(a_emails)
        a_email_correct = g["a_email_class"] == "correct"
        a_has_wrong = g["a_email_class"] in ("unrelated", "agency", "platform", "junk")
        a_has_channel = bool(a_email_correct or a_real_forms or a_real_socials
                             or (c["saved_people"] and i in (91, 9, 8)))  # people は実在のみ

        b_has_email = bool(g["b_emails"])
        b_has_form = bool(g["b_forms"])
        b_has_person = bool(g["b_people"])
        b_has_channel = bool(g["b_emails"] or g["b_forms"] or g["b_socials"] or g["b_people"])

        a_email += 1 if a_email_correct else 0
        a_email_wrong += 1 if a_has_wrong else 0
        a_email_wrong_cases += 1 if a_has_wrong else 0
        a_form += 1 if a_real_forms else 0
        a_channel += 1 if a_has_channel else 0
        a_official += 1 if g["a_official_correct"] else 0
        if g["a_email_class"] == "platform":
            a_platform_email += 1
        # System が saved した実在人物（KS UI 断片 FP は数えない）
        a_person += 0  # 実在人物として妥当に保存できた案件は 0（全て FP か未取得）

        b_email += 1 if b_has_email else 0
        b_form += 1 if b_has_form else 0
        b_person += 1 if b_has_person else 0
        b_channel += 1 if b_has_channel else 0
        b_official += 1 if g["b_official"] else 0

        # Claude だけ / System だけが得た「有効チャネル」
        if b_has_channel and not a_has_channel:
            claude_only += 1
        if a_has_channel and not b_has_channel:
            system_only += 1

        if not b_has_channel and not a_has_channel:
            r = "両者取得不可（真メーカー/連絡先が公開なし・個人）"
            fail_reasons[r] = fail_reasons.get(r, 0) + 1

        per_case.append((i, c["source_site"], (c["maker_name"] or "-")[:16],
                         g["a_email_class"], g["a_official_correct"], a_has_channel,
                         g["b_official"] is not None, b_has_channel, g["status"]))

    def pct(x): return f"{x}/{n} ({x/n:.0%})"

    print(f"=== 改善前ベースライン（gold set = 実在 {n} 案件・maker 重複排除・フィクスチャ除外）===")
    print(f"分母 N = {n}（サイト内訳は末尾）")
    print()
    print("【10 指標：A=現行 Contact Intelligence / B=通常 Claude 調査(実地確認)】")
    print(f" 1. メール取得案件数        A(正しい maker メール)= {pct(a_email)}  "
          f"B(実地確認メール)= {pct(b_email)}")
    print(f" 2. フォーム取得案件数      A(実フォーム)= {pct(a_form)}  B= {pct(b_form)}")
    print(f" 3. 何らかの営業チャネル    A= {pct(a_channel)}  B= {pct(b_channel)}")
    print(f" 4. 公式サイト特定率        A(正)= {pct(a_official)}  B(確定)= {pct(b_official)}")
    print(f" 5. 担当者取得率            A(実在人物)= {pct(a_person)}  B= {pct(b_person)}")
    print(f" 6. 誤メール率(誤メール含む案件) A= {pct(a_email_wrong_cases)}")
    print(f" 7. プラットフォーム運営メール誤採用数 A= {a_platform_email} 件（support@zeczec.com 等）")
    print(f" 8. Claude だけが有効チャネル取得 = {claude_only} 案件")
    print(f" 9. System だけが有効チャネル取得 = {system_only} 案件")
    print(" 10. 取得失敗理由分類:")
    for r, k in sorted(fail_reasons.items(), key=lambda x: -x[1]):
        print(f"     - {r}: {k} 案件")

    print("\n【案件別（A_emailクラス / A公式正 / Aチャネル / B公式 / Bチャネル / 確認状態）】")
    for i, site, mk, aec, aof, ach, bof, bch, st in per_case:
        print(f"  p{i:<4} {site:<10} {mk:<16} A[{aec:<15} off={str(aof)[0]} ch={str(ach)[0]}]"
              f" B[off={str(bof)[0]} ch={str(bch)[0]}] {st}")

    by_site: dict[str, int] = {}
    for i in ids:
        s = cand[i]["source_site"]
        by_site[s] = by_site.get(s, 0) + 1
    print(f"\nサイト内訳: {by_site}")
    print("注: B は 2026-07-18 に WebFetch で公式ページを literally 確認した公開情報のみ。"
          "blocked=bot/接続で未確認（成果に数えない）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
