# Gold set 監査ログ（Contact Intelligence 評価基盤）

作成: 2026-07-18 / 対象: `tests/contact_intel_eval/`

## 1. 判明した問題

### 1.1 `gold_frozen_24.json` — expected_* が全件空
- 24 案件すべてで `expected_emails / expected_socials / expected_official_sites /
  expected_forms / expected_people` が空、`manually_verified=0`。
- このファイル単体では TP/FP/FN・precision/recall/F1 を測定できない（正解ラベル不在）。
- 用途を **prediction snapshot（saved_* のみ）** に限定する。ground truth には使わない。

### 1.2 `gold_partial.json` — auto-label 由来の汚染
- `harness.py build-partial` は「公式サイト上の live `mailto:` を無条件で
  `expected_emails` かつ `manually_verified=True` に昇格」する素朴ヒューリスティック。
- これにより **maker でない第三者メールが正解へ誤混入**した。全件監査結果:

| project | email | 実クラス | 監査判定 |
|---|---|---|---|
| p109 | `admin@reurl.cc` | url_shortener | **汚染**: reurl.cc は URL 短縮サービス。その運営者アドレスで maker でない |
| p108 | `info@mediafol.io` | (unknown) | **汚染**: 人手検証(GOLD_B)で「第三者」と確認済み。maker でない |
| p105 | `moreshop07@gmail.com` | personal | **未確認**: GOLD_B は `b_emails=[]`（gmail 未確定）。verified に昇格不可 |
| p108 | `info@singlestep.com` | (unknown) | 正: maker 公式メール |
| p136 | `momtobabydj@naver.com` | personal | 正: 人手検証済み maker CEO 連絡先 |
| p4 | `info@greenlab.com` | (unknown) | 出所不明（24 gold 対象外） |

- `admin@reurl.cc` 誤ラベルの根本原因: **prediction（サイト巡回で拾った mailto）を
  そのまま expected に採用**したこと。reurl.cc はユーザー指定の必須除外ドメインでもある。

## 2. 対応（この監査での変更）

### 2.1 ground truth を新設（prediction から生成しない）
- 追加: `build_ground_truth.py`（authoring スクリプト・**saved_* を一切読まない**）→
  `gold_ground_truth.json` を生成。
- 出典は `gold_set_v1.py` の `GOLD_B`（2026-07-18 に WebFetch で公式ページを
  literally 確認した人手検証記録）。証拠 URL・証拠文・検証日時・検証者を各案件に付与。
- スキーマ B: `expected_direct_emails / expected_fallback_emails / expected_forms /
  expected_socials / expected_people / expected_official_site / plausible_unconfirmed_emails /
  expected_no_public_contact / verification_status / blocked_reason / evidence_urls /
  evidence_snippets / verified_at / verified_by`。
- `verification_status`: verified(14) / partially_verified(2) / blocked(3) / unresolved(5)。
  blocked・unresolved は precision/recall の分母から除外し「取得失敗」で別集計。

### 2.2 `gold_partial.json` の扱い
- **正解セットとして使用しない**（汚染のため）。ファイル自体は履歴として残すが、
  評価 v2（`eval_v2.py`）は参照しない。
- 汚染エントリ（`admin@reurl.cc` / `info@mediafol.io` / `moreshop07@gmail.com`）は
  ground truth へ昇格させない。`gold_ground_truth.json` では:
  - p109 `admin@reurl.cc` → 記載せず（`blocked_reason` に「短縮URL運営者=非maker」と明記）。
  - p108 `info@mediafol.io` → `expected_direct_emails` から除外（証拠文に第三者と明記）。
  - p105 `moreshop07@gmail.com` → `plausible_unconfirmed_emails`（採点対象外）へ。

### 2.4 GT補正 2026-07-18（p118 Hanboost・direct email 3件を昇格）
- 契機: Phase 2 second-pass crawl 導入後の GT 監査で、当初「公式にメール掲載なし」とした
  p118 の maker 公式ドメイン(hanboost.com)上に営業メール3件が実在すると判明。
- 追加した expected_direct_emails（**人手検証・maker_official・第三者ではない**）:

| email | 証拠URL | HTML断片 | ownership | role |
|---|---|---|---|---|
| sales@hanboost.com | https://www.hanboost.com/pages/contact | `<strong>Business Collboration</strong>:sales@hanboost.com` | maker_official | high |
| support@hanboost.com | https://www.hanboost.com/pages/contact | `<strong>General Inquiry: </strong>support@hanboost.com` | maker_official | support |
| marketing@hanboost.com | https://www.hanboost.com/ | `feel free to contact marketing@hanboost.com , or submit your Maker…` | maker_official | person |

- 変更ファイル: `build_ground_truth.py`（p118 の direct/evidence/snippets）→ `gold_ground_truth.json` を再生成。
- 修正種別: **手動検証（証拠URL＋HTML断片）**。auto-label ではない。数値目的の改変ではない。
- **変更しなかった真のFP（GTは正しい・追加せず）**:
  - p108 `info@mediafol.io` — 別ドメイン(mediafol.io≠singlestep.com)・Web/技術支援の第三者。
  - p111 `parenting@cw.com.tw` — 親会社/小売グループ(天下)・非メーカー。
  - p111 `support@zeczec.com` — crowdfunding_platform 運営メール。
- 精度影響（verified direct・entity）: TP 6→9 / FP 6→3 / FN 0、P 50.0%→75.0% / R 100% / F1 66.7%→85.7%。

### 2.3 数値の作り方
- **expected を数値改善目的で改変していない**。gold ラベルはシステム出力から自動生成しない。
- ドメイン一致だが公式ページ未掲載のメールは `plausible_unconfirmed_emails` として
  TP/FP どちらにも数えない（不当な FP/TP を避ける）。

## 3. 変更種別
- `gold_ground_truth.json` / `build_ground_truth.py` / `eval_v2.py`: **新規追加（手動検証転記）**。
- `gold_frozen_24.json` / `gold_partial.json` / `gold_candidates.json`: **無変更**
  （prediction snapshot として温存。汚染ファイルも履歴保持）。

## 4. 変更前後
- 変更前: 信頼できる正解ラベル 0 件（frozen は空・partial は汚染）。
- 変更後: 人手検証 ground truth 24 案件（verified 14 / partial 2 / blocked 3 / unresolved 5、
  direct email 6・fallback email 5）。precision/recall を分母付きで測定可能。

## 5. GT補正 2026-07-23: p96 놀로(Knollo) を unresolved → verified

### 5.1 補正理由
当初 p96 は「真メーカー未特定」として `unresolved`（precision/recall の分母外）にしていた。
これは**過小評価**だった。`놀로(Knollo)` は **스파크펫(SparkPet, sparkpetkorea.com)** の
ブランドで、以下 3 事業を持つ:

- **Knollo Store** — `knollo.store`（ペット用品コマース）← 公式サイトとして採用
- **Knollo Square** — `knollo.co.kr`（オフライン複合空間）
- **Knollo Play** — アプリ（Google Play `com.sparkpet.knollo`）

### 5.2 実地確認（2026-07-23 ライブ疎通・httpx）

| 項目 | 実測値 |
|---|---|
| `https://www.knollo.store` | HTTP **200** / 854,124 bytes / server=Vercel |
| TLS | TLSv1.3 / CN=`www.knollo.store` / 有効期限 2026-09-24 |
| `<title>` | `놀로 knollo \| 반려동물 간식·용품·케어 전문몰` |
| canonical | `https://www.knollo.store/` |
| bot protection | **なし**（cf-ray なし・Cloudflare なし・UA 差なし） |
| `https://www.knollo.co.kr` | HTTP **200** / `<title>놀로스퀘어</title>` / ページ内から `https://www.knollo.store` へリンク |

傍証（第三者ソース）: SparkLabs ポートフォリオ "Sparkpet (KNOLLO)"、
Google Play `com.sparkpet.knollo`、platum.kr/archives/199247（스파크펫の놀로アプリ出시報道）。

### 5.3 変更内容

| 項目 | 変更前 | 変更後 |
|---|---|---|
| `verification_status` | `unresolved` | **`verified`** |
| `expected_official_site` | なし | **`https://www.knollo.store`** |
| `maker_name` | `놀로(Nolo) maker (未特定)` | `놀로 (Knollo / 스파크펫)` |
| `blocked_reason` | `true maker not identified; …` | 削除 |
| `expected_fallback_emails` | `hi@/makerlive@brand-kr.com` | **据え置き（agency 判定を維持）** |
| `plausible_unconfirmed_emails` | なし | `CX@sparkpetkorea.com` |

- **代理店判定は維持**: `brand-kr.com` は運営代行で maker 直通ではないため `fallback` のまま。
- `CX@sparkpetkorea.com` は `knollo.store` 上に実在するが、**運営会社ドメインで maker 公式
  ドメイン(`knollo.store`)と不一致**のため `direct` に昇格させず `plausible` に留める
  （2.3 の方針どおり TP/FP どちらにも数えない）。
- **`status="resolved"` は使用していない**: 本 GT のスキーマ語彙は
  `verified | partially_verified | blocked | unresolved | no_public_contact` であり、
  `resolved` は `eval_v2.py` の `STRICT` / `PARTIAL` / `EXCLUDED` いずれにも属さず、
  集計から**黙って脱落**する。意図（＝分母に入れて採点する）を実現する値は `verified`。

### 5.4 変更ファイル
- `build_ground_truth.py` — p96 を unresolved ブロックから verified ブロックへ移動・証拠付与
- `gold_set_v1.py` — GOLD_B[96] を `blocked` → `verified`、`b_official` を付与
- 本ファイル（`GOLD_AUDIT_LOG.md`）

**再生成していない成果物**: `gold_ground_truth.json`（`build_ground_truth.py` の出力）と
`gold_frozen_24.json`（`eval_v2.py` の入力）はいずれも `.gitignore` 対象の生成/凍結物で、
**まだ旧 p96 レコードを保持している**。eval_v2 の数値へ反映するには
`python tests/contact_intel_eval/build_ground_truth.py` の実行と frozen の更新可否判断が別途必要。

### 5.5 verification_status 内訳の変化
- 変更前: verified 14 / partially_verified 2 / blocked 3 / unresolved 5
- 変更後: **verified 15 / partially_verified 2 / blocked 3 / unresolved 4**

## 6. GT補正 2026-07-23: p97 주부디자인 を unresolved → verified

### 6.1 補正理由
当初 p97 は「真メーカー未特定」として `unresolved`（precision/recall の分母外）にしていた。
p96 놀로 と同型の**過小評価**だった。`주부디자인` は **호정아이앤티(I&T)** の
**消費者向け流通ブランド**で、公式ショップは `jubudesign.com`。

自社サイトの説明文（逐語）:

> 주부디자인은 30년 이상을 스테인리스 와이어와 정사각형 압연 소재의 수납용품을
> 전문적으로 제조하는 '호정I&T'의 소비자 유통 브랜드입니다.

### 6.2 実地確認（2026-07-23・fetch のみ）

| 項目 | 実測値 |
|---|---|
| `https://jubudesign.com/` | HTTP **200** / UTF-8 / 240KB |
| `<title>` / `og:site_name` | `주부디자인` |
| canonical / og:url | `https://jubudesign.com/` |
| `http://` → | 301 で https へ |
| `www.` | 到達可・canonical は非 www |

事業者情報（`jubudesign.com` / `jubudesign.cafe24.com` / `/shopinfo/company.html` /
`/shopinfo/guide.html` の**全ページ共通**・HTML 逐語）:

| 項目 | 値 |
|---|---|
| 상호 | 호정아이앤티(I&T) |
| 대표자 | 이순향 외 1명（Brave snippet では 이순향 / 김용권） |
| 사업자등록번호 | **212-22-58903** |
| 통신판매업 신고 | 제 2016-인천부평-0766 호 |
| 주소 | 21315 인천광역시 부평구 새벌로 44 통일개발1층 |
| 메일 | judy@jubudesign.com |
| 전화 | 채팅상담만 가능 |

傍証: 네이버 블로그「와디즈 펀딩에서 최초 출시! 주부디자인 큐브EGI물받침싱크랙」
（큐브 시리즈は jubudesign.com に実在＝campaign 商品と shop 商品の一致）、
Instagram `@jubudesign_official`、SSG(신세계몰) ブランドページ。
競合する別の「주부디자인」事業者は 1 件も見つからなかった。

**未確認として残る点**: wadiz campaign 406038 のページ自体は 403 で開けず、Brave も
`site:wadiz.kr` を 0 件で返す。同一性は「maker 名の完全一致 + 上記の傍証」に依拠する。

### 6.3 公式 URL に `jubudesign.com` を採り、cafe24 サブドメインを採らない理由

`jubudesign.cafe24.com` は同一実体（事業者情報が完全一致）だが:

1. cafe24 側の **canonical / og:url が `https://jubudesign.com/` を指す**（自己宣言）
2. `so.registrable_domain("https://jubudesign.cafe24.com")` → **`cafe24.com`**
   （PSL に private suffix 登録がないため共有ホスティングドメインに潰れる）。
   一方 `cds._domain_of()` は `jubudesign.cafe24.com` を返し、**2 つの正規化が食い違う**。
3. その結果、公式を cafe24 サブドメインにすると実測で
   `judy@jubudesign.com` が **owner=unknown（third_party 扱い）**に落ちる。
   `jubudesign.com` なら **owner=maker**。

ブランドショップを公式に採る方針は p96 `놀로` → `knollo.store`（運営会社
`sparkpetkorea.com` ではない）と同型。

### 6.4 変更内容

| 項目 | 変更前 | 変更後 |
|---|---|---|
| `verification_status` | `unresolved` | **`verified`** |
| `expected_official_site` | なし | **`https://jubudesign.com`** |
| `maker_name` | `주부디자인 maker (未特定)` | `주부디자인 (호정아이앤티 I&T)` |
| `blocked_reason` | `true maker not identified; …` | 削除 |
| `expected_fallback_emails` | `real1@makerz.co.kr` | **据え置き（agency 判定を維持）** |
| `plausible_unconfirmed_emails` | なし | `judy@jubudesign.com` |

`judy@jubudesign.com` は公式ドメイン上に掲載確認済みだが役割の人手確認が未了のため
`direct` に昇格させず `plausible` に留める（2.3 の方針どおり TP/FP どちらにも数えない）。

### 6.5 verification_status 内訳の変化
- 変更前: verified 15 / partially_verified 2 / blocked 3 / unresolved 4
- 変更後: **verified 16 / partially_verified 2 / blocked 3 / unresolved 3**

strict 分母 15 → 16、expected_official あり 13 → 14。

### 6.6 既知の副作用: official precision が 1 件悪化する

System の現行予測は `https://jubudesign.cafe24.com` で、expected `https://jubudesign.com`
とは **3 つの比較方法すべてで不一致**になる:

| 比較方法 | 予測 | 期待 | 判定 |
|---|---|---|---|
| 文字列 | jubudesign.cafe24.com | jubudesign.com | 不一致 |
| `so.registrable_domain` | cafe24.com | jubudesign.com | 不一致 |
| `cds._domain_of` | jubudesign.cafe24.com | jubudesign.com | 不一致 |

これは **GT が正しくなった結果、実在の弱点（cafe24 サブドメイン正規化の欠如）が
可視化された**もので、数値を良く見せるために GT を曲げることはしない（2.3 の方針）。
canonical / og:url を用いた公式 URL 正規化は **別 PR** で扱う（本 PR は GT のみ）。

### 6.7 変更ファイル
- `build_ground_truth.py` — p97 を unresolved ブロックから verified ブロックへ移動・証拠付与
- `gold_set_v1.py` — `GOLD_B[97]` を `blocked` → `verified`、`b_official` を付与
- 本ファイル（`GOLD_AUDIT_LOG.md`）

**再生成していない成果物**: `gold_ground_truth.json` / `gold_frozen_24.json` はいずれも
`.gitignore` 対象の生成/凍結物で、**まだ旧 p97 レコードを保持している**（5.4 と同じ状況）。
eval_v2 の数値へ反映するには別途 `build_ground_truth.py` の実行と frozen の更新可否判断が必要。
