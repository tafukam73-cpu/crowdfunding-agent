---
name: maker-identity-verify
description: 商品を出しているメーカー本人（法人・ブランド）を同定し、別法人・代理店・OEM先との取り違えを防ぐ。「メーカーは誰？」「本人確認して」「この会社で合ってる？」「maker identity」「同名の別会社では？」と言われたときに使う。source_ownership.classify_domain を正本とし、名前の一致だけで同一法人と断定しないことが要点。
---

# メーカー本人確認（Maker Identity Verify）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## なぜ必要か

**同名・類似名の別法人に営業メールを送る事故を防ぐため**です。
「maker 名でググって最初に出た会社」は、しばしば無関係な大企業や同名の別会社です。
`source_ownership.MAJOR_UNRELATED_BRANDS` はまさにこの誤爆を防ぐための除外リストです。

## 正本は既存実装

```python
from app.services import source_ownership as so

ctx = so.Ctx(
    maker_name=...,        # プラットフォーム上の maker 名
    brand_name=...,        # ブランド名（異なることがある）
    product_title=...,     # 商品名
    official_domain=...,   # 確定済み公式ドメイン（あれば）
)
ownership = so.classify_domain(url_or_host, ctx)
# → Ownership(ownership_class, score, evidence[], rejection_reason)
#   ownership.is_maker  == ownership_class in ("maker_official", "maker_subdomain")
```

補助関数: `so.registrable_domain()` / `so.domain_token()` / `so.tokens()` / `so.host_of()`

分類用の既知集合（**自分で作らず、これを使う**）:
`CROWDFUNDING_PLATFORMS` / `CROWDFUNDING_MARKETING` / `URL_SHORTENERS` / `MESSENGERS` /
`KNOWN_AGENCIES` / `RETAILERS` / `PERSONAL_EMAIL` / `MAJOR_UNRELATED_BRANDS`

## 同定に使える一次ソース（優先順）

| 順位 | ソース | 強度 |
|---|---|---|
| 1 | campaign ページ内の maker 表記・所在地・運営者情報 | **最強**（プラットフォームが検証している） |
| 2 | campaign ページから直接リンクされた公式サイト | 強 |
| 3 | 公式サイトの JSON-LD `Organization`（`official_site_verifier.extract_site_identity`） | 強 |
| 4 | 公式サイトの会社概要 / Impressum / About | 中 |
| 5 | 検索結果から見つけたページ | **弱**（要 corroboration 2ソース） |

**5 だけを根拠に同定しないでください。** 検索駆動の同定は過去に第三者メール混入（FP）を起こしています。

## 名前一致だけで断定しない

`classify_domain` は名前トークン一致 × ドメイン分類 × 役割で判定します。
**名前が一致しただけでは maker 認定になりません。**

```
悪い判定: maker 名 "Aurora" → aurora.com が公式（❌ 無関係な同名企業の可能性）
良い判定: maker 名 "Aurora" → campaign ページから auroradevices.io へのリンクを取得
         → そのサイトの JSON-LD Organization.name == "Aurora Devices Inc."
         → 商品名も同サイト内に存在
         → maker_official (evidence 3点)
```

以下は**同一法人の証明になりません**。
- 名前が似ている / 同じ
- 同じ商品カテゴリを扱っている
- SNS で相互言及がある（代理店・パートナーの可能性）

## 取り違えやすい相手

| 相手 | 見分け方 |
|---|---|
| **代理店・ディストリビュータ** | `KNOWN_AGENCIES` に該当 / サイトに複数ブランドが並ぶ / "Distributor" 表記 |
| **OEM 製造元** | 商品は同じだがブランド名が違う |
| **小売** | `RETAILERS` に該当 / 購入導線しかない |
| **同名の別法人** | `MAJOR_UNRELATED_BRANDS` / 所在地・設立が食い違う |
| **プラットフォーム運営** | `CROWDFUNDING_PLATFORMS`（例: `support@zeczec` は運営であり maker ではない） |
| **クラファン支援代行** | `CROWDFUNDING_MARKETING` |

## 非ラテン語 maker 名

韓国語・中国語・日本語等の maker 名では、トークン一致が構造的に機能しません（既知のブロッカー）。
この場合は [nonlatin-maker-resolve](../nonlatin-maker-resolve/SKILL.md) に切り替えてください。
**ラテン文字前提のロジックで無理に判定して「不一致」と結論づけないこと。**

## 記録

```
maker_identity: Aurora Devices Inc.
  ownership_class: maker_official
  evidence:
    - https://www.kickstarter.com/projects/xxx/yyy  (campaign 内 maker 表記・外部リンク)
    - https://auroradevices.io/about  (JSON-LD Organization.name 一致)
  確認: 2026-08-05T07:20:00Z / method=playwright_fetch
  未確認: 法人登記情報（未取得）
```

同定できない場合は **`不明` と記録**します。`makers` テーブル・`company_researches` に
根拠なしの値を書かないでください。

## 禁止事項

- 検索1件だけで maker を確定する
- 名前一致のみで同一法人と断定する
- 代理店・小売・プラットフォーム運営を maker として登録する
- 非ラテン語名でトークン一致が空になった結果を「該当なし」と確定する

## 次の工程

→ [official-site-verify](../official-site-verify/SKILL.md)
