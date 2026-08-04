---
name: email-ownership-verify
description: メールアドレスの所有者が本当にそのメーカーかを検証し、第三者・代理店・プラットフォーム運営のアドレスへの誤送信を防ぐ。「このメアド送っていい？」「所有者を確認して」「第三者メールが混ざってる」「メールの信頼度は」と言われたときに使う。source_ownership.classify_domain と email_validation を正本とし、取得元不明のアドレスを unverified から格上げしないことが要点。
---

# メール所有者の検証（Email Ownership Verify）

前提として [evidence-ledger](../evidence-ledger/SKILL.md) の規約に従ってください。
先に [official-site-verify](../official-site-verify/SKILL.md) で公式ドメインを確定します。
**公式ドメインが未確定の状態で所有者判定はできません。**

## なぜ必要か

過去に**第三者メールの混入（FP）**が実際に発生しています。
クラファンページや検索結果には、以下が同居しています。

- プラットフォーム運営（`support@zeczec` など）
- クラファン支援代行・マーケ代理店
- 商品紹介メディア
- 無関係な同名企業

これらに営業メールを送ると、**信用を失い、取り返しがつきません**。

## 正本は既存実装

```python
from app.services import source_ownership as so
from app.services import email_validation as ev

# 1. 形式・ダミー判定
ev.is_valid_business_email(email)      # bool
ev.business_email_reason(email)        # 弾いた理由

# 2. ドメイン所有者の分類
ctx = so.Ctx(maker_name=..., brand_name=..., product_title=..., official_domain=...)
ownership = so.classify_domain(email, ctx)   # → Ownership
ownership.is_maker                            # maker_official / maker_subdomain

# 3. 取得元に基づく信頼度
ev.email_confidence(email=..., email_owner=..., sources=[...])
# → {"level": "high|medium|low|unverified|invalid", "label": 日本語}
```

**`official_domain` を Ctx に渡してください。** これが無いと所有者判定の精度が落ちます。

## 信頼度の意味（勝手に上げない）

`ev.CONFIDENCE_LABELS` の定義:

| level | 日本語 | 条件 | 送信可否 |
|---|---|---|---|
| `high` | 高信頼 | 公式ドメイン一致（contact / footer 由来） | ✅ |
| `medium` | 要確認 | 公式サイトの規約・プライバシーページ由来 | ⚠️ 要確認 |
| `low` | 低信頼 | クラファンページ由来 | ⚠️ 要確認 |
| `unverified` | 未検証 | **取得元不明（推測を含む）** | ❌ **送らない** |
| `invalid` | 無効 | ダミー / no-reply | ❌ |

> **推測で組み立てたアドレスは `unverified` です。**
> `info@<公式ドメイン>` を「たぶんある」で生成することは**禁止**です。実在確認は送信では行いません。

## 弾くべき所有者クラス

`classify_domain` の分類で以下に該当したら**送信対象にしません**。

| 集合 | 内容 |
|---|---|
| `CROWDFUNDING_PLATFORMS` | Kickstarter / Indiegogo / wadiz / zeczec 等の運営 |
| `CROWDFUNDING_MARKETING` | クラファン支援代行 |
| `KNOWN_AGENCIES` | 代理店・ディストリビュータ |
| `RETAILERS` | 小売 |
| `MESSENGERS` | メッセンジャー系ドメイン |
| `URL_SHORTENERS` | 短縮URL |
| `MAJOR_UNRELATED_BRANDS` | 無関係大企業 |
| `PERSONAL_EMAIL` | gmail/yahoo 等のフリーメール（→ 後述） |

### フリーメールの扱い

`PERSONAL_EMAIL` は**即失格ではありません**。小規模 maker は Gmail を公式窓口にしていることがあります。
ただし**公式サイト上に明記されている場合のみ**採用します。

```
✅ 採用: https://example.com/contact に "contact@gmail.com" と記載 → source_url あり
❌ 不採用: 検索結果に出てきた Gmail アドレス → 所有者を証明できない
```

## 保存

```
contact_people.email_source   ← どこで見つけたか（official_site_contact / campaign_page 等）
contact_people.source_url     ← 実際に取得したページのURL
contact_people.confidence     ← 信頼度
contact_people.updated_at     ← 確認日時
```

**`source_url` が空のメールアドレスを保存しないでください。** 検証不能なデータは負債です。

## 判定の記録例

```
email: care@aurora-devices.io
  ownership_class: maker_official
  confidence: high (高信頼)
  source_url: https://aurora-devices.io/contact
  email_source: official_site_contact
  確認: 2026-08-05T07:20:00Z / method=playwright_fetch
  evidence: 公式ドメイン一致 / contact ページの記載

email: support@zeczec.com
  判定: 不採用（CROWDFUNDING_PLATFORMS = プラットフォーム運営）
```

## 禁止事項

- アドレスを推測で組み立てる（`info@`, `sales@`, `firstname.lastname@` の生成）
- 取得元不明のアドレスを `low` 以上に格上げする
- `source_url` なしで `contact_people` に保存する
- 公式ドメイン未確定のまま所有者判定を確定する
- 実在確認のためにテスト送信する

## 次の工程

→ [email-role-classify](../email-role-classify/SKILL.md)（誰宛かの妥当性）
