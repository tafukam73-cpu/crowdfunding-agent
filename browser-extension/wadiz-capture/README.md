# Wadiz Contact Capture（Crowdfunding Agent 連携 Chrome 拡張）

Wadiz の商品ページは Akamai により自動取得（httpx / headless Playwright）が 403 になります。
この拡張は **Akamai 回避ではありません**。ユーザーが通常の Chrome で商品ページを閲覧し、
「もっと見る」を手動展開した状態の **表示中の公開 DOM** を取得して、ローカルの
Crowdfunding Agent（localhost）に取り込みます。

- Cookie / localStorage / sessionStorage / 認証トークン / フォーム入力値は取得しません。
- 送信先は **localhost のみ**（それ以外へは送信しません）。
- 生 HTML は保存せず、抽出結果・content hash・証拠周辺だけを保存します。
- 推測メールは生成しません。`@wadiz.kr`（運営）だけを除外し、外部ドメイン
  （Gmail / Naver / 独自ドメイン等）でメーカー本文に明示されたメールは保存できます。

## 事前準備（バックエンド/フロントの起動確認）

Crowdfunding Agent のバックエンドが起動している必要があります。

```
# リポジトリ直下で
docker compose ps          # cfagent-backend / cfagent-db が Up であること
curl http://localhost:8000/health   # 200 が返ること（backend 稼働確認）
```

（フロントで結果を確認したい場合は http://localhost:3000 も起動）

## インストール手順（開発者モード）

1. Chrome で `chrome://extensions` を開く
2. 右上の「デベロッパーモード」を **ON**
3. 「パッケージ化されていない拡張機能を読み込む」をクリック
4. このフォルダ `browser-extension/wadiz-capture` を選択
5. 拡張機能のアイコンがツールバーに表示されます

## 使い方（コピー・貼り付け不要）

1. Chrome で Wadiz 商品ページ（`https://www.wadiz.kr/web/campaign/detail/...`）を開く
2. ページ上で「もっと見る（더보기）」を **手動で展開**
   - 展開位置が分からない場合は、拡張の「「もっと見る」候補をハイライト」を押すと候補を強調します
3. 拡張機能のアイコンを押す
4. 「表示中の公開情報を取得」を押す
   - 未展開の可能性がある場合は警告が出ます（展開して再取得してください）
5. 対象プロジェクトが自動特定されます（複数/不一致の場合は選択・入力。**自動確定はしません**）
6. 「抽出プレビュー」を押すと、メール・SNS・公式サイト・メーカー名・除外理由・既存との差分が表示されます
7. 保存したい項目にチェック（footer/nav 由来や既存メールは既定でオフ）
8. 「確認して保存」を押すと、確認した項目だけが非破壊で保存され、Contact Intelligence に反映されます

保存後、Crowdfunding Agent の案件詳細 / Sales Copilot で連絡先状態・decision に反映されます
（再評価は明示 POST / バックグラウンドジョブ。GET 表示中に重い処理は起動しません）。

## 権限（最小限）

- `activeTab`, `scripting`: アクティブな Wadiz タブの表示中 DOM を取得するため
- `host_permissions`: `https://www.wadiz.kr/*`（取得元）, `http://localhost:8000/*`,
  `http://localhost:3000/*`（ローカルの Crowdfunding Agent への送信のみ）

## 送信されるデータ

`source_url` / `title` / `text` / `html`(outerHTML) / `links` / `mailtos` / `tels` /
`meta` / `json_ld` / `captured_at`。いずれも **表示中の公開情報のみ**。
サーバー側の preview では DB を変更せず、confirm（ユーザー確認後）でのみ保存します。

## テスト（純粋関数）

```
node browser-extension/wadiz-capture/lib.test.js
```

URL 正規化 / campaign ID 抽出 / localhost 送信制限 / 未展開警告判定を検証します。

## 手動検証手順（実データ）

1. 上記インストール後、メールが掲載されている Wadiz 商品ページを開く
2. 「もっと見る」を展開 → 拡張で取得 → プレビュー
3. 目視で見えているメーカーのメールが「抽出メール」に出ることを確認
4. チェックして保存 → 案件詳細の Contact Intelligence / Sales Copilot に反映されることを確認

> この環境（AI 側）からは実ブラウザを操作できないため、実ページからの抽出は
> 上記手順でユーザーが確認します。取得できた JSON または画面結果でメールが
> 抽出できたことを確認して完了とします（0 件は成功扱いにしません）。
