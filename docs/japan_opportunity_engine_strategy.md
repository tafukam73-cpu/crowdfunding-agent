# Japan Opportunity Engine 設計書

> ステータス: **設計ドキュメント（実装なし・コード変更なし）**
> 位置づけ: Discovery Engine v1-5 の次段（[[discovery_engine_strategy]] の
> 「Japan Opportunity Engine foundation」を具体化）
> 最終更新: 2026-07-03

Discovery Engine で発掘した海外クラウドファンディング商品を「日本市場で売れるか／
営業すべきか」で評価し、Contact Intelligence・Sales Opportunity へ進める前の
**意思決定エンジン**を設計する。本書は方針の共通認識づくりが目的で、コードは追加しない。

---

## 1. Japan Opportunity Engine の目的

- Discovery Engine で見つけた海外クラファン商品を、**日本市場向けに評価**する。
- 「**日本で売れる可能性**」と「**営業すべき優先度**」を一つのスコアに統合して判断する。
- Contact Intelligence（連絡先探索）／ Sales Opportunity（営業案件化）へつなぐ前の
  **意思決定エンジン**（＝「この商品を追うべきか、どう攻めるか」を決める層）にする。

既存との関係:
- 入力は `discovered_products`（発掘商品）と、その AI Discovery Scoring 結果。
- 出力は「機会（opportunity）」の判定＝優先度・推奨戦略・推奨アクション・根拠。
- Discovery Scoring（v1-2）が「発掘の一次スクリーニング」なのに対し、本エンジンは
  **日本市場に特化した二次評価＋事業シナリオ提示**を担う。役割が重なる軸
  （japan_fit / crowdfunding_fit 等）は Scoring の結果を**入力として再利用**し、
  二重計算を避ける。

---

## 2. 全体フロー

```
Discovery Product          … 海外CFから発掘（discovered_products）
      ↓
AI Discovery Scoring       … 多軸の一次スクリーニング（v1-2・既存）
      ↓
Japan Opportunity Engine   … 日本市場特化の二次評価＋事業シナリオ（本書・新設）
      ↓
Contact Intelligence       … メーカー特定・連絡先探索（既存 v3/v5）
      ↓
Sales Opportunity          … 営業案件化・交渉ステータス（既存）
      ↓
営業活動                    … メール/フォーム/SNS での実アプローチ
```

各段は前段の出力を入力にする。Japan Opportunity Engine は「Contact Intelligence を
始める価値があるか」を判定するゲートを兼ねる（低スコアはここで保留にできる）。

---

## 3. 評価軸

すべて **0〜100 点・高いほど日本展開／営業に有利**（リスク系も「高い＝リスクが低い＝
安全」で統一）。各軸は AI 評価＋ルールベースのどちらでも算出でき、`confidence` を持つ。

### 3.1 日本市場適合度（japan_market_fit_score）
- **何を評価するか**: 日本の生活様式・住環境・嗜好への適合。
- **高評価**: 小型・軽量・省スペース、日用品・キッチン・収納・アウトドア・文具など、
  日本の住宅事情や贈答文化に合う。
- **低評価**: 大型・大電力前提、北米サイズ前提、日本で用途が想像しにくい。
- **必要なデータ**: category / product_name / description、寸法・重量（あれば）。

### 3.2 日本未進出可能性（japan_entry_gap_score）
- **何を評価するか**: 日本にまだ入っていない度合い（＝機会の大きさ）。
- **高評価**: 日本語公式・日本法人・代理店・主要ECいずれにも見当たらない。
- **低評価**: Amazon.co.jp / 楽天等で販売中、日本語公式や代理店が既にある。
- **必要なデータ**: §4 の未進出判定（既存 `japan_sales_check` / availability を活用）。

### 3.3 クラファン適性（crowdfunding_fit_score）
- **何を評価するか**: Makuake / GREEN FUNDING / CAMPFIRE 等で話題化しやすいか。
- **高評価**: 新規性・ストーリー性・ビジュアル訴求、海外CFでの実績（支援者数・調達額）。
- **低評価**: 日用品すぎて目新しさが無い、実績が極端に低い。
- **必要なデータ**: backers_count / funding_amount / novelty、description。
- **再利用**: Discovery Scoring の `crowdfunding_fit_score` を入力に取り込む。

### 3.4 一般販売適性（retail_fit_score）
- **何を評価するか**: クラファン後の**通常物販（EC・小売・卸）**での継続性。
- **高評価**: リピート性・消耗性・実用性が高く、定番化しやすい。適正価格帯。
- **低評価**: 一発ネタ、極端に高単価、サポート負荷が高い。
- **必要なデータ**: category / price 帯 / 消耗性の推定、description。

### 3.5 規制リスクの低さ（regulatory_safety_score）
- **何を評価するか**: §5 の日本の許認可・輸入規制の軽さ。
- **高評価**: 規制カテゴリに該当せず、そのまま輸入・販売しやすい。
- **低評価**: PSE / 技適 / 薬機 / 食品衛生 等に該当し、認証・届出が必要。
- **必要なデータ**: category / description のカテゴリ判定（§5 のキーワード）。
- **原則**: **断定しない**。「該当の可能性 → 確認が必要」として低評価＋根拠を残す。

### 3.6 輸送しやすさ（logistics_score）
- **何を評価するか**: 輸入・国内配送のしやすさ。
- **高評価**: 小型軽量・常温・非危険物、モジュール梱包が容易。
- **低評価**: 大型・重量物、リチウム大容量電池、液体・可燃・冷蔵など。
- **必要なデータ**: 寸法・重量（あれば）、category（大型バッテリー・危険物判定）。

### 3.7 利益率見込み（margin_potential_score）
- **何を評価するか**: 想定仕入（CF 価格帯）と日本想定売価の差＝粗利ポテンシャル。
- **高評価**: 原価に対し日本売価を十分に取れる（ブランド・希少性・付加価値）。
- **低評価**: 価格競争が激しい、関税・送料・認証費で粗利が薄い。
- **必要なデータ**: funding_amount / 想定売価モデル・関税/送料/認証費の概算。
- **注**: 初期はレンジ推定（粗い）でよい。confidence を低めに持たせる。

### 3.8 競合リスクの低さ（competition_gap_score）
- **何を評価するか**: 日本国内の競合の少なさ（＝入り込む余地）。
- **高評価**: 類似品が少ない／代替が弱い／カテゴリ自体が新しい。
- **低評価**: 大手・PB・100均等の代替が豊富。
- **必要なデータ**: 日本語検索の類似商品ヒット状況（search_fn）、category。

### 3.9 営業成功可能性（sales_success_score）
- **何を評価するか**: メーカーが日本展開・独占販売に応じる見込み。
- **高評価**: 連絡先の質が高い（Sales/Partnership 窓口）、企業規模・海外展開姿勢。
- **低評価**: 連絡手段が乏しい、個人・多忙、日本志向が薄い。
- **必要なデータ**: Contact Intelligence の探索結果（contactability_score 等）。
- **注**: Contact Intelligence 未実行時は「不明」＝中立＋低 confidence とする（§6）。

### 3.10 総合 Japan Opportunity Score（overall_opportunity_score）
- **何を評価するか**: 上記を重み付き合成した**総合的な日本機会スコア**。
- **算出（初期案・ルールベースの重み。合計 1.0）**:
  - japan_market_fit 0.20 / japan_entry_gap 0.15 / crowdfunding_fit 0.12 /
    retail_fit 0.10 / regulatory_safety 0.12 / logistics 0.08 /
    margin_potential 0.10 / competition_gap 0.08 / sales_success 0.05
- **confidence が低い軸は寄与を減衰**（情報不足の軸で総合が過大にならないように）。
- 一覧の既定ソートキー。高いほど「今すぐ営業すべき」。

---

## 4. 日本未進出判定

「日本にまだ入っていないか」を複数ソースで確認し、`japan_entry_gap_score` と
`japan_presence_summary` の根拠にする。既存の `japan_sales_check`（5サイト検索・
未上陸/可能性あり/販売済み）と availability 判定の仕組みを土台に**拡張**する。

| 確認対象 | 何を見るか | 進出ありのシグナル |
|---|---|---|
| 日本語公式サイト | `.jp` / 日本語ページの有無 | 日本語の製品ページ・購入導線がある |
| 日本法人 | 日本法人・オフィスの記載 | 「株式会社」「日本支社」等の記載 |
| 日本代理店 | 総代理店・正規販売店の告知 | 「日本正規代理店」「独占販売」の記載 |
| Amazon.co.jp | 商品名/ブランド検索 | 出品あり（並行輸入含むが要区別） |
| 楽天市場 | 商品名/ブランド検索 | 出店・出品あり |
| Yahoo!ショッピング | 商品名/ブランド検索 | 出品あり |
| Makuake | プロジェクト検索 | 既に日本でクラファン実施済み |
| GREEN FUNDING | プロジェクト検索 | 既に日本でクラファン実施済み |
| CAMPFIRE | プロジェクト検索 | 既に日本でクラファン実施済み |
| 日本語レビュー記事 | ブログ/メディア | 日本語のレビュー・紹介記事が多数 |
| 日本語SNS投稿 | X / Instagram 等 | 日本語での言及・購入報告が多い |

判定方針:
- **未上陸 / 可能性あり / 日本販売済み** の3値（既存 availability に準拠）＋根拠明細。
- 並行輸入・転売は「正規進出」と区別（可能性ありに寄せる）。
- 各ソースは `search_fn` 注入で差し替え可能にし、**実ネットワークなしでテスト**する。

---

## 5. 法規制リスク

日本で輸入・販売する際に確認が必要な代表カテゴリ。**該当＝販売不可ではない**。
「**確認・認証・届出が必要**」を示し、`regulatory_safety_score` を下げ、
`regulatory_summary` に理由を残す（断定しない）。

| カテゴリ | 主対象 | 論点（要確認事項） |
|---|---|---|
| PSE | 電源・充電器・電池製品 | 電気用品安全法。菱形/丸形 PSE、届出事業者 |
| TELEC（技適） | 無線（Bluetooth/Wi-Fi 等） | 電波法の技術基準適合証明が必要 |
| 食品衛生法 | 食品・飲料・食器・調理器具 | 輸入届出、器具・容器包装の規格 |
| 薬機法 | 効能をうたう機器・健康関連 | 医薬品/医薬部外品/医療機器の該当性 |
| 化粧品 | スキンケア・メイク・美容液 | 化粧品製造販売業許可・成分規制 |
| 医療機器 | 診断・治療をうたう機器 | クラス分類・認証/承認 |
| 子ども向け製品 | 玩具・乳幼児用品 | 安全基準（ST等）・小部品・化学物質 |
| 大型バッテリー | ポータブル電源・大容量電池 | PSE・輸送（UN38.3）・消防法 |
| 刃物・危険物 | ナイフ・スプレー・化学品 | 銃刀法・消防法・輸送規制 |

判定は §3.5 と連動。既存 Discovery Scoring の要注意カテゴリ辞書
（medical / wireless / large battery / knife 等）を再利用・拡張する。

---

## 6. スコアリング方針

- **0〜100 点**。高いほど営業・日本展開に有利（リスク軸も「高い＝安全」で統一）。
- **AI 評価＋ルールベース評価の併用**。AI（ai_fn）が使えれば採用し、未指定・例外・
  不正 JSON のときは**ルールベースにフォールバック**（例外を投げない）。
- **AI 失敗時は安全にフォールバック**し、必ず全軸のスコアを返す（既存
  discovery_scoring_service と同じ堅牢設計を踏襲）。
- **根拠説明を必ず保存**（各 summary ＋ opportunity_reasoning ＋ evidence_json）。
- **confidence を持たせる**（軸ごと＋総合）。情報が薄い軸は confidence を下げ、
  総合スコアへの寄与を減衰させる。
- **「不明」は 0 点ではなく中立（≒50）または低 confidence** として扱う。未実行の
  Contact Intelligence 由来の営業成功可能性などは「不明＝中立＋低 confidence」。
- スコアは AI/ルールいずれの経路でも 0〜100 に正規化（clamp）。

---

## 7. DB 設計案（将来追加）

新規テーブル `japan_opportunity_analyses`（1 発掘商品につき複数回の分析を履歴保持）。
既存 `discovered_products` は変更しない（緩く discovered_product_id で参照）。

| カラム | 型（想定） | 説明 |
|---|---|---|
| id | int PK | |
| discovered_product_id | int index | `discovered_products.id` を参照（緩い連携） |
| japan_market_fit_score | int null | 日本市場適合度 |
| japan_entry_gap_score | int null | 日本未進出可能性 |
| crowdfunding_fit_score | int null | クラファン適性 |
| retail_fit_score | int null | 一般販売適性 |
| regulatory_safety_score | int null | 規制リスクの低さ |
| logistics_score | int null | 輸送しやすさ |
| margin_potential_score | int null | 利益率見込み |
| competition_gap_score | int null | 競合リスクの低さ |
| sales_success_score | int null | 営業成功可能性 |
| overall_opportunity_score | int null index | 総合 Japan Opportunity Score |
| confidence_score | int null | 総合 confidence（0〜100） |
| japan_presence_summary | text null | 日本進出状況の要約（§4 の根拠） |
| competition_summary | text null | 競合状況の要約 |
| regulatory_summary | text null | 規制の要約（「確認が必要」を明記） |
| logistics_summary | text null | 物流の要約 |
| pricing_summary | text null | 価格・粗利の要約 |
| opportunity_reasoning | text null | 総合評価の理由 |
| recommended_strategy | text null | 推奨戦略（CF先行/総代理店/EC直販 等） |
| recommended_next_action | text null | 推奨する次の一手 |
| evidence_json | json null | 根拠明細（ソース・URL・軸別 confidence 等） |
| created_at | datetime | |
| updated_at | datetime | |

補足:
- 軸別 confidence は `evidence_json` に格納（カラム増を避ける）。
- マイグレーションは**追加のみ**（既存テーブル無変更）。連携は
  `discovered_products.contact_discovery_id` と同様の緩い参照方針。

---

## 8. API 設計案（将来追加）

既存 `/discovery/*` を壊さず、`/japan-opportunity` プレフィックスで追加する。

| メソッド | パス | 機能 |
|---|---|---|
| POST | `/japan-opportunity/analyze/{discovered_product_id}` | 分析を実行して保存し、結果を返す（`ai_fn`/`search_fn` 未注入時はルール＋モック） |
| GET | `/japan-opportunity/analyses` | 分析一覧（product・min_score・sort でフィルタ／総合スコア順） |
| GET | `/japan-opportunity/analyses/{id}` | 分析詳細（全軸・summary・evidence） |
| POST | `/japan-opportunity/analyses/{id}/create-sales-opportunity` | 分析を起点に Sales Opportunity を作成（Contact Intelligence 連携経由） |

方針:
- analyze は同期実行でよい（ネットワークは注入関数経由・既定はモック/ルール）。
- 商品なし→404、分析なし→404、入力不備→400（既存ルータの流儀に合わせる）。

---

## 9. UI 設計案

Discovery 画面（`/discovery`）のカード拡張、または専用画面
`/japan-opportunity`（一覧＋詳細）で以下を表示する。

- **Japan Opportunity Score**（総合・大きく表示・色分け）
- **日本未進出可能性**（未上陸/可能性あり/販売済み バッジ＋根拠リンク）
- **法規制リスク**（該当カテゴリのタグ＋「要確認」注記。断定しない）
- **競合状況**（競合の多寡・代表例）
- **推奨戦略**（CF 先行 / 総代理店 / EC 直販 等）
- **推奨次アクション**（次の一手）
- **根拠一覧**（軸別スコア・confidence・参照ソース）
- **Contact Intelligence 開始**（既存 v1-5 の連携ボタンを再利用）
- **営業案件化**（Sales Opportunity 作成ボタン）

UI 原則:
- スコアは `NN/100` または「未評価」。confidence 低は淡色/注記で明示。
- 既存 slate/Tailwind スタイル・日本語表示に合わせる（[[discovery_engine_strategy]] 準拠）。

---

## 10. ロードマップ

| バージョン | 内容 |
|---|---|
| **v1-1** | 設計ドキュメント（本書） |
| **v1-2** | DB・モデル・API 土台（`japan_opportunity_analyses` ＋ CRUD、空実装で保存/取得） |
| **v1-3** | ルールベース評価（全軸をルールで算出・根拠と confidence 付き） |
| **v1-4** | AI 評価連携（ai_fn 注入・失敗時ルールへフォールバック） |
| **v1-5** | Discovery UI 統合（`/discovery` へスコア・根拠・アクションを表示） |
| **v1-6** | Sales Opportunity 統合（分析 → 営業案件化の導線） |
| **v2** | 自動ランキング・毎日発掘（スケジューラで発掘→分析→上位通知） |

各段は「小さく実装→テスト→コミット」で 1 段ずつ積む。

---

## 11. 実装原則

1. **小さく実装してコミット**（1 バージョン=1 増分。RED→GREEN→commit）。
2. **既存機能を壊さない**（既存 API・画面・テストを無回帰。追加のみ・後方互換）。
3. **外部 API なしでもテスト可能**（sqlite ＋ `python tests/test_xxx.py` 形式を踏襲）。
4. **fetch_fn / search_fn / ai_fn を注入可能**にする（取得・検索・AI をすべて差し替え可）。
5. **実ネットワークなしでテスト可能**（既定はモック/ルール。注入時のみ外部アクセス）。
6. **AI 失敗時は安全にフォールバック**（未指定・例外・不正 JSON でもルール評価で全軸を返す）。
7. **法規制は断定せず「確認が必要」**として扱う（該当＝販売不可ではない。根拠を残す）。

---

## 付録: 既存資産の再利用マップ

| 本エンジンの要素 | 再利用する既存資産 |
|---|---|
| 発掘商品・スコア入力 | `discovered_products` / `discovery_scoring_service`（v1-2） |
| 日本未進出判定 | `japan_sales_check` / availability（5サイト検索・3値判定） |
| 規制カテゴリ辞書 | `discovery_scoring_service` の要注意カテゴリ（medical/wireless/large battery 等） |
| 連絡先・営業成功可能性 | Contact Intelligence（`contact_discoveries.contactability_score` 等・v3/v5） |
| 営業案件化 | Sales Opportunity（`sales_opportunities`）＋ Discovery→CI 連携（v1-5） |
| 注入設計・フォールバック | `discovery_crawler`（fetch_fn 注入）/ `discovery_scoring`（ai_fn＋ルール） |

関連: [[discovery_engine_strategy]]
