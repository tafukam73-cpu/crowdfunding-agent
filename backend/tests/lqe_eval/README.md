# LQE 除外判定 評価セット（lqe_eval）

営業対象除外判定（Lead Qualification Engine）が

- 営業すべき案件を**誤って除外していないか**
- 本当に**不要な調査を減らせているか**
- **Evidence 不足**による block が多すぎないか
- **サイトごとの差**があるか

を実測するための評価セットです。

## `contact_intel_eval` とは別物

| | `contact_intel_eval/` | **`lqe_eval/`（本ディレクトリ）** |
|---|---|---|
| 測るもの | **連絡先探索の精度**（official site / email の precision・recall） | **営業対象判定の精度**（除外の正しさ） |
| サンプル | 「公式サイトも連絡先も取れなかった困難ケース」に意図的に偏らせた 30 件 | 営業対象母集団からの**層化サンプル** 30 件 |
| 最重要指標 | precision / recall | **過剰除外率** |

**両者を混ぜないでください。** `contact_intel_eval` の 30 件は探索が難しいケースに
偏っており、除外判定の評価には使えません（営業可能な案件がほとんど含まれないため、
最大リスクである過剰除外率を測れない）。

`contact_intel_eval/` には**保護対象の未追跡 9 ファイル**があります（CLAUDE.md §6）。
本ディレクトリは完全に分離しており、あちらには一切触れません。

## 構成

```
lqe_eval/
  README.md          このファイル
  _select_cases.py   ケース選定ツール（**一度だけ使う**。DB 読み取り専用）
  cases.json         signals スナップショット（fixture・凍結済み）
  ground_truth.json  人手ラベル
  run_eval.py        評価実行（DB 非依存・qualify() を直接呼ぶ）
  report.py          Markdown レポート生成
  generated/         **生成物（.gitignore 済み・commit しない）**
    eval_result.json
    eval_report.md
```

`generated/` を commit しない理由: 実行のたびに `generated_at` が変わり差分がノイズに
なるためです。レポートは必要なときに再生成してください。

## 実行

```bash
docker compose exec -T backend python tests/lqe_eval/run_eval.py
docker compose exec -T backend python tests/lqe_eval/report.py
docker compose exec -T backend python tests/test_lqe_eval.py   # ハーネス自体の検証
```

`run_eval.py` は **人手レビューが未完了なら非 0 終了**します
（`Ground Truth incomplete: N/30 reviewed`）。途中経過を見るときだけ
`--allow-incomplete` を付けてください。

ケースを選び直す場合のみ（通常は不要）:

```bash
docker compose exec -T backend python tests/lqe_eval/_select_cases.py --dry-run
docker compose exec -T backend python tests/lqe_eval/_select_cases.py
```

## 制約（テストで固定）

- 評価は **DB へアクセスしない**（`gather_signals` / `run()` を呼ばない）
- **`qualify()` を直接呼ぶ**（純粋関数）
- **外部 HTTP を行わない**
- **Ground Truth を自動で書き換えない**
- **LQE 本体のルールを変更しない**（評価で問題を見つけても修正は別 PR）
- 返信率・成功率・可能性予測を算出しない

## 同一メーカーの重複排除（canonical_maker_key）

単一項目では割れるため、次の優先順で**すべての候補キー**を作り、
**いずれかが既出なら同一メーカー**とみなします。

1. `maker_url` / `creator_url`
2. `official_site` の正規化 host
3. `maker_name` の正規化値
4. campaign URL の creator 部分
5. campaign URL 全体

正規化: scheme 除去 / `www.` 除去 / lowercase / trailing slash 除去 /
query・fragment 除去 / Unicode NFKC / `maker_name` は空白・記号差を吸収。

**`project_id` はキーに使いません。** `maker_name` が NULL の案件同士を
同一メーカー扱いしません。

> 実データで判明した注意点: campaign URL から creator を取れるのは
> Kickstarter と Indiegogo だけです。Wadiz（`/web/campaign/detail/<id>`）と
> Zeczec（`/projects/<id>`）は URL に作者が現れないため creator キーを作りません。
> ここを誤ると**全案件が同一メーカー扱い**になります。

## キャンペーンの新旧（campaign_age_bucket）

`ended` / `live` / `unknown` の 3 値です。

> **実データには 180 日以上前に終了した案件が 1 件もありません**（収集が直近のみ）。
> そのため「1 年以上前かどうか」では区分できず、「募集終了済み / 募集中 / 不明」で
> 分けています。閾値をいじって古い案件があるように見せかけていません。
>
> `unknown` は Zeczec に集中します（`end_date` を持つのは全 67 件中 1 件）。
> **`unknown` を新旧どちらかへ丸めません。** 情報不足そのものを評価対象とします。

## サンプル 30 件

サイト層化: Kickstarter 10 / Indiegogo 5 / Wadiz 10 / Zeczec 5

条件: `campaign_url` あり / `archived_at IS NULL` / `canonical_maker_key` 重複なし /
カテゴリ偏りを抑制 / clear・review・blocked 候補を混在 /
通貨混在のため `raised_amount` の単純ソートは使わない（決定的な SHA-256 順で選定）。

| case_id | site | project_name | maker_name | canonical_maker_key | key_source | age | category | 選定理由 |
|---|---|---|---|---|---|---|---|---|
| LQ01 | kickstarter | CoachBoard Pro Player developm | sayeed | `url:kickstarter.com/profile/cra…` | maker_url_or_creator_url | live | Apps | blocked/blocked 候補・partially_verified |
| LQ02 | kickstarter | Roman Applied Sciences / Music | Roman Kiefer | `url:kickstarter.com/profile/nod…` | maker_url_or_creator_url | live | Software | blocked/blocked 候補・partially_verified |
| LQ03 | kickstarter | Commodore 64: The Birth of a C | Nicola Caulfield & | `url:kickstarter.com/projects/gr…` | maker_url_or_creator_url | unknown | Documentary | blocked/blocked 候補・partially_verified |
| LQ04 | kickstarter | 8 Dragons | Wonderbow Games | `url:kickstarter.com/projects/wo…` | maker_url_or_creator_url | unknown | Tabletop Gam | blocked/blocked 候補・unresolved |
| LQ05 | kickstarter | Hanboost T1 Pocket Size Laser  | Hanboost | `host:hanboost.com` | official_site_host | ended | Product Desi | clear/clear 候補・partially_verified |
| LQ06 | kickstarter | Aldo, Giovanni e Giacomo: File | 3DClever | `url:kickstarter.com/profile/3dc…` | maker_url_or_creator_url | live | 3D Printing | clear/blocked 候補・unresolved |
| LQ07 | kickstarter | Gossip Buzz: safe social that  | JKulDev LLC | `url:kickstarter.com/profile/jku…` | maker_url_or_creator_url | live | Apps | clear/blocked 候補・unresolved |
| LQ08 | kickstarter | WARSUN T9 Pro: 4-in-1 Magnetic | warsunofficial | `url:kickstarter.com/profile/war…` | maker_url_or_creator_url | live | Gadgets | clear/blocked 候補・partially_verified |
| LQ09 | kickstarter | ADSBee Winglet! | Pants for Birds LL | `url:kickstarter.com/profile/pan…` | maker_url_or_creator_url | live | Hardware | clear/blocked 候補・partially_verified |
| LQ10 | kickstarter | HercShirt V5.0: First Tee That | HercLéon America | `name:hercléonamerica` | maker_name | live | Product Desi | clear/blocked 候補・partially_verified |
| LQ11 | indiegogo | MIRA Dial - The Focus Tool You | MIRA Labs | `host:relycars.com` | official_site_host | ended | Tech & Innov | clear/clear 候補・partially_verified |
| LQ12 | indiegogo | ZhongYi Zone T1 : The Dreamy F | ZhongYi_Optics | `host:zyoptics.net` | official_site_host | live | Tech & Innov | clear/clear 候補・partially_verified |
| LQ13 | indiegogo | GPD G2 eGPU: The World's First | GPD HK | `name:gpdhk` | maker_name | ended | Tech & Innov | review/blocked 候補・partially_verified |
| LQ14 | indiegogo | COINAX:Coin-Sized Magnetic Ti  | Coinax Tools Limit | `name:coinaxtoolslimited` | maker_name | live | Tech & Innov | review/blocked 候補・partially_verified |
| LQ15 | indiegogo | Solar Energy system | RGSolar | `name:rgsolar` | maker_name | live | Tech & Innov | clear/blocked 候補・unresolved |
| LQ16 | wadiz | [글로벌4억] 완전 무선의 자유, 한국인에게 딱 맞는  | 녹프리유한회사 | `host:nocfree.kr` | official_site_host | ended | 주변기기 | clear/clear 候補・partially_verified |
| LQ17 | wadiz | [누적 3.5억] 양대면 상태로 접히는 세상에 없던 절 | 하이브리드유모차 | `host:nextbaby.co.kr` | official_site_host | ended | 출산·육아용품 | clear/clear 候補・partially_verified |
| LQ18 | wadiz | 보냉칸+노트북 수납까지 '뭘 담아도 예쁜' 포모드 남녀 | 포모드 주식회사 | `name:포모드주식회사` | maker_name | live | 가방 | clear/blocked 候補・partially_verified |
| LQ19 | wadiz | 700개 기업 매출 성장 노하우! 지니어스 숫자경영 시 | 지니어스 컴퍼니 | `name:지니어스컴퍼니` | maker_name | live | 경제·경영 | clear/blocked 候補・unresolved |
| LQ20 | wadiz | 해외 대란템ㅣ3초면 충분해요. 24시간 지속 혁신적 립 | 얼리언스 | `name:얼리언스` | maker_name | live | 메이크업 | clear/blocked 候補・unresolved |
| LQ21 | wadiz | 8월배송ㅣ완벽방수 스티커/3초 완성! 각인한듯 깔끔하게 | 디자인느낌 | `name:디자인느낌` | maker_name | live | 문구 | clear/blocked 候補・partially_verified |
| LQ22 | wadiz | [6in1] 헤드만 톡! 바꾸면 피부 고민 끝, 차세대 | ADAM V | `name:adamv` | maker_name | live | 뷰티디바이스 | clear/blocked 候補・unresolved |
| LQ23 | wadiz | [신제품] 인바디 KOROT V1 출시! 집에서 관리하 | (주)인바디헬스케어 | `name:주인바디헬스케어` | maker_name | live | 생활가전 | clear/blocked 候補・unresolved |
| LQ24 | wadiz | [카드지갑 전원증정!]스마트AI녹음기: 미팅·회의·수업 | 맥파이테크 | `name:맥파이테크` | maker_name | live | 스마트가전 | clear/blocked 候補・unresolved |
| LQ25 | wadiz | 미피 덕후 심장 저격! 붙이고 노는 <미피 패브릭 자석 | Many a Little | `name:manyalittle` | maker_name | live | 애니메이션 | clear/blocked 候補・partially_verified |
| LQ26 | zeczec | 兩週有感見證 🦷 INOPRO 牙齒淨白貼片｜牙醫師好評推薦 | MORESIE | `url:moresie.com` | maker_url_or_creator_url | unknown | 挺好店 | clear/blocked 候補・unresolved |
| LQ27 | zeczec | 再登場《那個夏天的風，又回來了》巧福寶島電風｜40週年紀念・ | 巧福健康家電 | `url:shop.unionchen.com.tw` | maker_url_or_creator_url | unknown | 科技 | clear/clear 候補・unresolved |
| LQ28 | zeczec | 再登場✨親子天下【史上最強經絡疏通棒】居家必備，獨家磁珠導熱 | 親子天下Shopping | `url:shopping.parenting.com.tw` | maker_url_or_creator_url | unknown | 設計 | clear/clear 候補・partially_verified |
| LQ29 | zeczec | 全數完售🙌 LG MoodMate小暮光｜最百變的氣氛夥伴， | LG | `url:lg.com/tw/projectors/cinebe…` | maker_url_or_creator_url | ended | 科技 | review/blocked 候補・unresolved |
| LQ30 | zeczec | 最好的翻譯是無痕的！【 Globitalks｜AI 智慧翻譯 | (なし) | `campaign:zeczec.com/projects/gl…` | campaign_url | unknown | (なし) | review/blocked 候補・partially_verified |

## Ground Truth の運用

人手ラベルは `ground_truth.json` に置きます。必須項目・手順は
[ground-truth-audit](../../../.claude/skills/ground-truth-audit/SKILL.md) を参照してください。

- `verification_status` は `verified` / `partially_verified` / `unresolved`
- **`unresolved` は率の分母から除外**し、件数を別掲します
- `should_research` / `should_allow_outreach` は **True / False / `null`（不明）** の 3 値。
  **`null` を True/False へ丸めません**（分母から除外します）
- `reviewer` / `reviewed_at` / `reviewer_reason` は**必須**
- `evidence_urls` が空の場合は `evidence_notes` に理由が必要

**現在の状態**: AI がラベル**案**を作成した段階で、人の承認は未完了です。
全件が `partially_verified`（18 件）または `unresolved`（12 件）で、
**`verified` は 0 件**です。`run_eval.py` は `--allow-incomplete` なしでは
非 0 終了します。

## 指標（15 種・すべて分子/分母を先に表示）

| # | 指標 | 意味 |
|---|---|---|
| 1 | **過剰除外率** | 人手で「調査すべき」なのに `pre_research=blocked` にした割合。**最重要** |
| 2 | 誤送信許可率 | 人手で「送るべきでない」なのに `pre_outreach=clear` にした割合 |
| 3 | blocker precision | LQE が blocked にしたうち人手も blocked と判断した割合 |
| 4 | review 適合率 | 同上（review） |
| 5 | clear 適合率 | 同上（clear） |
| 6 | Evidence 充足率 | blocker/review の Finding に 4 点セットが揃っている割合 |
| 7 | 停止理由別件数 | A〜T コード別 |
| 8 | サイト別集計 | 4 サイト |
| 9 | stage 別集計 | pre_research / pre_outreach |
| 10 | 調査削減量 | blocked 件数 − 過剰除外 = **純削減件数** |
| 11 | 人手確認必要率 | review の割合 |
| 12 | internal_db 依存率 | 根拠が内部 DB 参照だけの案件の割合 |
| 13 | stale 率 | 鮮度切れ Finding を持つ案件の割合 |
| 14 | override 必要候補率 | 送ってよいのに clear でない割合 |
| 15 | 判定不能率 | 人手でも証跡不足と判断した割合 |

**N=30 と小さいため、率は必ず `分子/分母` を先に読んでください。**
分母 0 は `N/A（分母0）` と表示します。

## 時間換算について

「調査削減量」の主指標は**件数**です。処理時間の換算は、同一環境での実測
（測定件数・測定日時・平均・中央値を明記）が無い限り出しません。推定値を
主指標にしません。
