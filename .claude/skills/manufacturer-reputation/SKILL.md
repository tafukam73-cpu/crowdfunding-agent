---
name: manufacturer-reputation
description: メーカーの信頼性を、過去クラファン実績・発送履歴・支援者評価・企業実体から事実ベースで収集する。「このメーカー信用できる？」「過去の実績は？」「ちゃんと発送してる？」「評判は？」「初めてのクラファン？」と言われたときに使う。発送遅延・未発送・炎上履歴は営業判断に直結する。噂・印象での信頼性判定を禁止する。
---

# メーカー信頼性調査（Manufacturer Reputation）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。
先に [maker-identity-verify](../maker-identity-verify/SKILL.md) で**maker を同定済み**であること。
**同定前に評判を調べると、別法人の評判を拾います。**

## なぜ営業判断に効くか

Makuake に持ち込む以上、**発送できないメーカーを紹介すると自社の信用が毀損します**。
過去に未発送・大幅遅延を起こしたメーカーは、適性が高くても見送る判断があり得ます。

同時に、**良い実績は営業本文の説得材料**になります（「前回◯◯で成功実績あり」）。

## 収集項目

### A. 過去のクラウドファンディング実績

| 項目 | 取得元 | 記録 |
|---|---|---|
| 過去プロジェクト数 | Kickstarter / Indiegogo の creator ページ | プロジェクトURL |
| 各プロジェクトの達成状況 | 同上 | 達成額・支援者数・確認日時 |
| **発送済みか** | プロジェクトの Updates / コメント欄 | 該当 Update のURL |
| 出荷予定からの遅延 | 予定日 vs 実出荷の Update | 双方のURL |

`projects.maker_url` にプラットフォーム上の maker ページがあります。ここが一次ソースです。

**「初回プロジェクト」も重要な事実です。** 実績なし＝失格ではありませんが、
発送リスクとして明示的に記録してください。

### B. 発送履歴・支援者の反応（最重要）

過去プロジェクトの**コメント欄・Updates が一次ソース**です。ここを読まずに信頼性は語れません。

- [ ] 直近 Update の日付（更新が止まっていないか）
- [ ] 支援者からの未発送・遅延の訴えがあるか
- [ ] メーカーが遅延を説明・対応しているか（**対応の有無が質を分ける**）
- [ ] 返金対応の記録があるか

```
悪い記録: 「評判は良さそう」
良い記録: 「前作 XXX（2025-03 出荷予定）は 2025-09 出荷。6ヶ月遅延。
          遅延理由を Update #12 で説明済み・返金対応あり。
          根拠: https://www.kickstarter.com/projects/xxx/yyy/posts/12345
          確認: 2026-08-05T07:20:00Z」
```

### C. 企業としての実体

| 項目 | 取得元 |
|---|---|
| 設立年・所在地 | 公式サイト会社概要 / Impressum |
| 法人格の有無 | 同上（Inc. / Ltd. / GmbH 等の表記） |
| 従業員規模の示唆 | About / Team ページ |
| 継続稼働の証拠 | 公式サイトの直近更新・SNS の直近投稿 |

**公式サイトが消滅・長期未更新のメーカーは連絡不能リスク**が高く、
[lead-disqualify](../lead-disqualify/SKILL.md) の差し戻し対象になり得ます。

### D. ネガティブ情報

- [ ] 訴訟・炎上・詐欺疑惑の報道（**報道URLを取得できた場合のみ**記録）
- [ ] プラットフォームからの中止・凍結履歴
- [ ] 知的財産権侵害の指摘

**噂・掲示板の書き込みを根拠にしないでください。** 一次ソースか、確認可能な報道のみです。
ネガティブ情報は特に慎重に。誤った記録は不当な評価になります。

## 収集手段

```python
from app.services import company_research_service   # 企業調査（company_researches に保存）
from app.services import search_providers           # Brave Search
from app.services import web_research_service
```

既存テーブル: `company_researches`（`official_site_url` / `project_url` / `sources` JSON 付き）

過去プロジェクトの取得には `app/scrapers/` の既存スクレイパを使います
（`kickstarter.py` / `indiegogo.py` 等）。**取得は job 経由・並列起動禁止**（CLAUDE.md §5）。

## 判定の型（スコア化しない）

**信頼性を点数化してユーザーに見せないでください**（CLAUDE.md §1 の予測値禁止に抵触します）。
事実の列挙と、営業上のリスクの明示に留めます。

```
メーカー信頼性: Aurora Devices Inc.
  過去実績   : Kickstarter 2件（2023年・2025年）
               根拠 https://www.kickstarter.com/profile/xxx  (2026-08-05T07:20Z)
  発送       : 2023年案件は出荷完了（Update #18 で完了報告）
               2025年案件は出荷予定 2026-01 → 未出荷（Update は 2026-06 で停止）
               根拠 https://.../posts/98765
  企業実体   : 米国法人・2021年設立・公式サイト稼働中
  ネガティブ : 確認範囲では発見なし（※不在の証明ではない）

  営業上のリスク: 進行中案件が未出荷・Updateが2ヶ月停止
                  → 提携前に出荷状況の確認が必要
```

## 「見つからなかった」の扱い

ネガティブ情報が見つからないことは**潔白の証明ではありません**。
「確認範囲では発見なし」と書き、検索した範囲を明示してください。

## 禁止事項

- maker 同定前に評判を調べる（別法人の評判を拾う）
- 噂・匿名掲示板を根拠にする
- 信頼性をスコア化してユーザーに提示する
- 「実績なし＝信用できない」と断定する（初回案件は正常）
- ネガティブ情報を根拠URLなしで記録する

## 関連

- [maker-identity-verify](../maker-identity-verify/SKILL.md)（前提工程）
- [competitor-intelligence](../competitor-intelligence/SKILL.md)
- [lead-disqualify](../lead-disqualify/SKILL.md)（連絡不能・重大リスク時の差し戻し先）
