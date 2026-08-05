---
name: nonlatin-maker-resolve
description: 韓国語・中国語・日本語など非ラテン文字のメーカー名で、トークン一致による公式サイト推定が構造的に機能しない場合の代替手順。「韓国のメーカーで公式サイトが出ない」「significant_terms が空」「中国語の社名で見つからない」「wadiz 案件の maker が特定できない」と言われたときに使う。ラテン文字前提ロジックの限界を認識し、別経路で根拠を取る。
---

# 非ラテン語 maker 名の解決（Non-Latin Maker Resolve）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## この問題の性質

`source_ownership.tokens()` / `domain_token()` はラテン文字のトークン分割を前提としています。
maker 名が **한글 / 中文 / かな漢字** の場合、有意なトークンが生成されず、
**ドメインとの名前一致が構造的に成立しません**（既知のブロッカー）。

**これは「公式サイトが存在しない」ことを意味しません。** ロジックが判定できないだけです。
`不一致` として処理して失格にすると、実在する案件を取りこぼします。

## 検出（この経路に入るべきか）

以下のいずれかに該当したら、通常の [official-site-verify](../official-site-verify/SKILL.md) から切り替えます。

- maker 名にラテン文字が実質含まれない
- `so.tokens(maker_name)` が空 or 1文字トークンのみ
- 商品元プラットフォームが wadiz / zeczec / makuake 等の非英語圏
- 検索クエリを組んでも結果が0件・無関係ばかり

## 代替経路（優先順）

ラテン文字一致に頼らない根拠を取ります。

### 1. campaign ページの外部リンクを直接使う（最優先）

名前一致を経由しません。**maker 自身が貼ったリンクは名前照合が不要**です。
[product-page-capture](../product-page-capture/SKILL.md) で取得した外部リンクを
`official_site_verifier.verify_candidate()` に掛けます。この経路が最も成功率が高い。

### 2. プラットフォーム上の maker プロフィール

`projects.maker_url` に事業者情報が載ることがあります。
韓国 wadiz / 台湾 zeczec は**事業者情報の開示欄**を持つことがあり、
法人名・事業者登録番号・所在地が取れます。`wadiz_import_service.py` / `zeczec_enrichment_service.py`
に既存の取り込み実装があります。

### 3. 英文表記・ローマ字表記を一次ソースから取得する

**自分で音訳しないでください。** 推測の音訳は別法人にヒットします。

| 取得元 | 例 |
|---|---|
| 商品パッケージ・ロゴの英字表記（画像・仕様表） | `주식회사 아우로라` → ロゴに `AURORA` |
| campaign ページ内の英語併記 | About セクション |
| SNS アカウントのハンドル名 | `@aurora_kr` |
| 商品名の英字表記 | 型番・製品名 |

英字表記が**一次ソースから取れた場合のみ**、それをキーに通常経路へ戻れます。
根拠として「どこで英字表記を確認したか」を必ず残します。

### 4. 現地語での検索

Brave 検索を**現地語のまま**実行します（`search_providers.py`）。
英訳して検索すると別法人に当たります。

```
韓国語 maker → 韓国語のまま検索 + "공식" (公式) / "홈페이지" (ホームページ)
中国語 maker → 中国語のまま検索 + "官网" (公式サイト)
```

### 5. 商品型番での検索

社名を経由せず、**型番・製品名で公式サイトに到達する**経路です。
型番は言語非依存のため、非ラテン語問題を回避できます。

## 判定の緩和はしない

経路を変えても、**確定に必要な根拠の水準は下げません**。

- `verify_candidate` の `verdict == "official"` が必要
- 検索経由なら **2ソース corroboration** が必要
- 「韓国のサイトだから正しいだろう」は根拠ではない

## 記録

```
official_site_url: 不明（非ラテン語 maker 名のため通常経路が適用不可）
  maker_name: 주식회사 아우로라
  試した経路:
    1. campaign 外部リンク → 記載なし
    2. wadiz 事業者情報   → 取得できず（ログイン要求）
    3. 英字表記の取得     → 仕様表に "AURORA DEVICES" を確認
       根拠: https://www.wadiz.kr/web/campaign/detail/xxxxx (2026-08-05T07:20Z)
    4. "AURORA DEVICES" で再検索 → 候補2件、いずれも candidate 止まり
  結論: 候補どまり。確定せず
```

## 禁止事項

- 音訳・翻訳を自分で行い、それをキーに公式サイトを確定する
- トークン一致が空だったことを「公式サイトなし」と結論づける
- 非ラテン語案件だからと corroboration 要件を緩める
- 現地語圏のドメイン（.kr / .cn / .tw）というだけで公式と推定する

## 関連

- [official-site-verify](../official-site-verify/SKILL.md)（通常経路）
- [maker-identity-verify](../maker-identity-verify/SKILL.md)
