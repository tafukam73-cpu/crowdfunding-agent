---
name: official-site-verify
description: メーカーの公式サイトを確定する。ECモール・ニュース・ディレクトリ・代理店サイトを公式と誤認しないための検証手順。「公式サイトどれ？」「official_site_url を確定して」「このサイト公式？」「公式サイトが見つからない」と言われたときに使う。official_site_verifier.verify_candidate を正本とし、campaign_url での代用を厳禁とする。
---

# 公式サイト確認（Official Site Verify）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。
先に [maker-identity-verify](../maker-identity-verify/SKILL.md) で maker を同定します。

## 最重要の禁止事項

> **`official_site_url` に `campaign_url` を入れてはいけません。逆も同様です。**（CLAUDE.md §5）

クラファンのキャンペーンページは公式サイトではありません。これを代用すると、
後続のメール所有者判定（ドメイン一致）が全て壊れます。

## 正本は既存実装

```python
from app.services import official_site_verifier as osv

osv.verify_candidate(
    url=candidate_url,
    html=fetched_html,            # 取得済みHTML（未取得なら None → rejected 相当）
    maker_name=...,
    product_name=...,
    source_site_domain=...,       # クラファン側のドメイン（自己参照除外用）
) -> dict   # {"url", "verdict", "confidence", "evidence", ...}
```

### verdict の意味（実装のdocstringより）

| verdict | 意味 | 扱い |
|---|---|---|
| `"official"` | 素性が maker/ブランド/商品名と一致（confidence high/medium） | ✅ 確定してよい |
| `"candidate"` | 取得できたが確定に足る一致が無い（confidence low） | ⚠️ **候補のまま**。確定しない |
| `"rejected"` | ECモール / ディレクトリ / ブログ / 取得不能 | ❌ 公式にしない |

**`candidate` を `official` に格上げしないでください。** 追加の根拠を得るまで候補です。

### `site_role` で相手の正体を区別する

`verify_candidate()` は `site_role` を返します。**`maker` 以外は公式サイトではありません。**

| site_role | 意味 | 公式サイトとして採用 |
|---|---|---|
| `maker` | メーカー本人のサイト | ✅ |
| `blog` | ブログプラットフォーム上の記事 | ❌ |
| `reseller_like` | 小売店・取扱店・代理店 | ❌（→ 別チャネルとして記録は可） |
| `unknown` | 判定不能 | ❌ |

さらに以下も別物として区別します。
- **platform**: クラファン内プロフィール（`campaign_url.is_platform_host()` で判定）
- **directory**: 企業DB・SNS・サイトビルダー（`osv.is_directory()`）

補助判定: `osv.is_marketplace()` / `osv.is_directory()` / `osv.is_news()` /
`osv.is_blog_platform()` / `osv.has_reseller_hint()` / `osv.looks_reseller_page()`
素性抽出: `osv.extract_site_identity(html)` → JSON-LD `Organization` / `<title>` 等

## gate や url_state の `official_site_url` を信用しない

`contact_search_gate.evaluate()` や `campaign_url.url_state()` が返す
`official_site_url` は **`projects.maker_url` 由来**です。これは確定値ではありません。

```
❌ gate の official_site_url をそのまま採用する
✅ 候補として受け取り、必ず verify_candidate() で再検証する
```

`campaign_url.is_platform_host()` によりクラファン内プロフィールは除外されますが、
**除外されなかったからといって公式サイトである保証はありません**。
`maker_url` が単に未設定（`None`）のことも多くあります。

## 候補の集め方（優先順）

| 順位 | 取得元 | 強度 | 備考 |
|---|---|---|---|
| 1 | **campaign ページ内の外部リンク** | 最強 | maker 自身が貼ったリンク |
| 2 | campaign ページの maker プロフィール（`projects.maker_url`） | 強 | |
| 3 | 商品ページ内の SNS リンク先のプロフィール欄 | 中 | |
| 4 | Brave 検索（maker 名 + 商品名） | 弱 | **必ず取得して verify_candidate に掛ける** |

**1 を飛ばして 4 から始めないでください。** 検索駆動は第三者サイトの混入（FP）を起こします。

## 公式と誤認しやすいもの

`verify_candidate` が `rejected` にする対象と、人が間違えやすい対象:

| 種類 | 例 | 判定 |
|---|---|---|
| EC モール | Amazon / 楽天 / AliExpress | `is_marketplace` → rejected |
| ディレクトリ | 企業DB・まとめサイト | `is_directory` → rejected |
| ニュース | TechCrunch 等の紹介記事 | `is_news` → rejected |
| **代理店サイト** | 複数ブランドを扱う販社 | `source_ownership.KNOWN_AGENCIES` で判定 |
| **小売の商品ページ** | ブランド名を冠していても小売 | `RETAILERS` |
| クラファン支援代行 | `CROWDFUNDING_MARKETING` | rejected |
| URL 短縮 | `URL_SHORTENERS` | 展開してから判定 |

## 確定に必要な根拠の数

| 状況 | 必要な根拠 |
|---|---|
| campaign ページから直接リンク **かつ** サイト素性が maker 名と一致 | 1ソースで確定可 |
| 検索経由で発見 | **2ソース必要**（corroboration）。`test_official_recall_corroborated_domain.py` の思想 |
| 非ラテン語 maker 名 | [nonlatin-maker-resolve](../nonlatin-maker-resolve/SKILL.md) へ |

## 保存

```
contact_discoveries.v2_official_site_url     ← 確定したURL
contact_discoveries.v2_official_site_source  ← どの経路で確定したか（重要）
contact_discoveries.v2_primary_source_url    ← 一次ソースURL
contact_discoveries.v2_researched_at         ← 確認日時
contact_discoveries.searched_urls            ← 探索した全URL（JSON）
```

`v2_official_site_source` を空のまま確定しないでください。**経路が残らない確定は検証不能**です。

## 見つからない場合

**「見つからなかった」は正当な結論です。** 無理に候補を昇格させないでください。

```
official_site_url: 不明
  探索済み: https://... (campaign 外部リンク無し)
           https://... (検索1位・is_news で rejected)
           https://... (検索2位・is_marketplace で rejected)
  確認: 2026-08-05T07:20:00Z
  次の手: nonlatin-maker-resolve（maker 名が非ラテン文字のため）
```

## 禁止事項

- `campaign_url` を `official_site_url` として保存する
- `candidate` を根拠なく `official` に昇格させる
- 取得せずに（html=None で）公式判定する
- URL を推測で組み立てる（`https://<makername>.com`）
- 代理店・小売サイトを公式として登録する

## 次の工程

→ [email-ownership-verify](../email-ownership-verify/SKILL.md)（公式ドメインが確定して初めてメール所有者判定が可能）
