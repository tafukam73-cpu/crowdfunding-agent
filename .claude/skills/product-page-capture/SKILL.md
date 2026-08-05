---
name: product-page-capture
description: クラファン商品ページ（campaign_url）を漏れなく取得し、後続の判定に使える形で保存する。「商品ページを取って」「ページの中身を全部見て」「仕様が知りたい」「商品内容を把握して」と言われたときに使う。campaign_url と official_site_url を絶対に混同しないこと、JS描画ページはPlaywrightで取得すること、取得できなかった範囲を明示することが要点。
---

# 商品ページの完全取得（Product Page Capture）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。

## まず前提：4つの URL を混同しない

CLAUDE.md §5 の通り、以下は**すべて別物**です。取り違えると誤った連絡先を生みます。

| 概念 | カラム | このスキルの対象 |
|---|---|---|
| **商品ページ（キャンペーン）** | `projects.source_url` / `contact_discoveries.v2_campaign_url` | ✅ **これ** |
| maker の公式サイト | `contact_discoveries.v2_official_site_url` | ❌ → [official-site-verify](../official-site-verify/SKILL.md) |
| maker ページ（プラットフォーム内） | `projects.maker_url` | 補助的に使う |
| 画像・動画 | `projects.image_url` / `.video_url` | 参考 |

**`official_site_url` を `campaign_url` として使わないでください。逆も同様です。**

## 取得手段の使い分け

このプロジェクトには取得系サービスが複数あります。**用途が違うので使い分けます。**

| サービス | 用途 | 使う場面 |
|---|---|---|
| `app/scrapers/fetcher.py` | プラットフォーム別スクレイパの共通取得 | **まずこれ**。makuake / kickstarter / indiegogo / greenfunding 等の専用実装がある |
| `document_reader_service.py` | ページ本文の読解・要約 | 本文から事実を抽出したいとき |
| `recursive_crawl_service.py` | サイト内の再帰クロール | 公式サイト側の探索（**campaign ページには使わない**） |
| `product_context_service.py` | 日本語要約・特徴抽出 | 取得後の整形 |

プラットフォーム別スクレイパ（`app/scrapers/`）: `makuake.py` `kickstarter.py` `indiegogo.py`
`greenfunding.py` `jp_success_base.py` 等。**既存スクレイパがあるプラットフォームは必ずそれを使ってください**
（HTML構造の知見が入っています）。

## fetcher の指定を省略しない（実案件で 403 になった）

`get_fetcher()` の**既定は `"httpx"`** です。設定を読んでくれません。
既定のまま Kickstarter を取得すると **HTTP 403** で失敗します（実案件 #151 で確認）。

```python
from app.config import settings
from app.scrapers.fetcher import get_fetcher

fetch = get_fetcher(
    settings.scrape_fetcher,                      # ★ 必ず渡す（既定は httpx）
    rate_limit_seconds=settings.scrape_rate_limit_seconds,
    timeout=settings.scrape_timeout_seconds,
    retries=settings.scrape_retries,
)
```

Kickstarter / Indiegogo など JS 描画・ボット対策のあるサイトでは
**Playwright 指定が必須**です（`SCRAPE_FETCHER=playwright`）。

## JS 描画ページの扱い

多くのクラファンページは JS 描画です。`SCRAPE_FETCHER` の設定に従い、
Playwright（Chromium、インストール済み）で取得します。

**重要（CLAUDE.md §5）**:
- 重い取得を**同期 POST で起動しない**。必ず job 経由
- **full job と子 job を並列起動しない**（Chromium が増殖して backend が無応答になった実績あり）

`SCRAPE_RATE_LIMIT_SECONDS` / `SCRAPE_TIMEOUT_SECONDS` / `SCRAPE_RETRIES` を尊重してください。
レート制限を外して速く回そうとしないこと。

## 取得すべき範囲（チェックリスト）

1ページ取って終わりにしないでください。以下が揃って「完全把握」です。

- [ ] メイン本文（ストーリー・説明文）
- [ ] **技術仕様表**（→ 規制判定に必須。[jp-market-fit](../jp-market-fit/SKILL.md)）
- [ ] リターン／価格帯・SKU
- [ ] 出荷予定・配送対象国（**日本が含まれるか**）
- [ ] FAQ・コメント欄（日本の代理店に関する言及が出ることがある）
- [ ] maker 名・ロゴ・所在地表記（→ [maker-identity-verify](../maker-identity-verify/SKILL.md)）
- [ ] 外部リンク（**公式サイト候補の一次ソース**。→ [official-site-verify](../official-site-verify/SKILL.md)）
- [ ] 資金調達状況（達成額・支援者数・終了日）

## 取得成功をバイト数で判定しない

実案件 #151 では Playwright が **1,160,369 バイト**を取得しましたが、
本文はプラットフォーム共通ナビゲーションが大半で、**商品ストーリーも仕様表も
含まれていませんでした**（`Bluetooth` / `mAh` / `Battery` / `Specification` すべて不在）。

> **バイト数は取得成功の指標になりません。**

### full / partial / failed の判定基準

| 判定 | 条件 |
|---|---|
| **full** | タグ・script・style 除去後の**本文実文字数**が十分（目安 2,000 文字以上）**かつ**必須項目（本文・仕様表・価格）がすべて取れている |
| **partial** | 本文は取れたが必須項目に欠落がある（例: 仕様表が JS 遅延読込で未取得） |
| **failed** | HTTP エラー / 本文実文字数が極端に少ない / 必須項目が全滅 |

```python
import re, html as ht
body = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', raw)
body = re.sub(r'(?s)<[^>]+>', ' ', body)
body = re.sub(r'\s+', ' ', ht.unescape(body))
len(body)   # ← これで判定する。len(raw) では判定しない
```

**partial のまま [jp-market-fit](../jp-market-fit/SKILL.md) へ進まないでください。**
仕様表が無い状態で技適・PSE を判定すると、それは推測になります。

## 取得できなかった範囲は必ず明示する

**部分取得を「取得済み」と扱わないでください。**

```
取得結果: 部分取得
  取得できた: 本文 / 価格 / 資金調達状況
  取得できなかった: 技術仕様表（"Tech Specs" タブが JS 遅延読み込みで未取得）
  影響: 技適・PSE の判定不能 → jp-market-fit は保留
  source_url: https://www.kickstarter.com/projects/xxx/yyy
  確認: 2026-08-05T07:20:00Z / method=playwright_fetch
```

仕様表が取れないまま規制判定に進むと、**推測で規制該当性を断定する**ことになります。禁止です。

## 保存

- 探索したURL → `contact_discoveries.searched_urls`（JSON）
- 取得日時 → 該当手法の `*_researched_at`
- 抽出事実 → `product_facts_service.build(db, project)` の経路に載せる

## 禁止事項

- URL を組み立てて生成する（`https://<maker>.com` の推測など）
- 取得できなかった項目を、他ページの情報や一般知識で埋める
- レート制限・タイムアウト設定を無効化する
- campaign ページに `recursive_crawl_service` を掛ける（クラファンサイト全体を這ってしまう）

## 次の工程

→ [product-facts-extract](../product-facts-extract/SKILL.md)
