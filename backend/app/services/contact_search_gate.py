"""日本クラファン適性ゲート（メール探索の事前判定）。

日本のクラウドファンディングに適さない商品へメール探索（Contact Intelligence）を
走らせないための関門。**新しいスコア体系は作らない**：日本クラファン適性スコアは
既存の ``sales_assessment_service`` の makuake_fit（Makuake 適性＝日本クラファン
再ローンチ適性）をそのまま使い、除外カテゴリの判定は ``discovery_scoring_service``
のキーワード表を再利用する。

通過条件（すべて満たすこと）:
  1. campaign_url（海外クラファン商品ページ）が source_site と整合して取得できている
  2. 日本語の商品概要があり、商品内容が判別できる
  3. 日本クラファン適性スコアが閾値以上
  4. 一般消費者向けの物理商品である（アプリ/SaaS・映画/ゲーム/書籍/音楽・イベント/
     寄付・B2B 専用・大型/重量物・医療効果訴求・武器/危険物 などは除外）
  5. 日本での訴求点（問題解決 / 利便性 / デザイン性 / 新規性 のいずれか）を説明できる

適性が低い案件は削除せず ``not_eligible`` / ``needs_review`` に分類する。

**表示方針**: 内部スコア（japan_crowdfunding_score）はゲート判定と並び順のために
保持するが、ユーザー向け画面には出さない。画面には ``user_reasons``（探索しなかった
具体的理由。確認可能な事実・ルールに基づく文言）だけを表示する。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.project import Project
from app.services import campaign_url as campaign_url_mod
from app.services.category_keywords import (
    CAUTION_KEYWORDS,
    HIGH_FIT_KEYWORDS,
    match_categories,
)

logger = logging.getLogger("contact_search_gate")

# --- 閾値（1 か所に集約。散在させない） ------------------------------------- #
# 日本クラファン適性スコア（sales_assessment の makuake_fit）の下限。
# これ以上でメール探索へ進める。ランキングや既存案件のスコア自体は変更しない
# （ゲートは「探索してよいか」の判定のみに使う）。
JAPAN_CF_SCORE_THRESHOLD = 45
# スコアが閾値未満でも、この点数までは「要確認（needs_review）」として人が判断できる。
# これを下回ると明確に対象外（not_eligible）。
JAPAN_CF_SCORE_REVIEW_FLOOR = 30
# 日本語概要としてこの文字数未満は「商品内容が判別できない」とみなす。
MIN_SUMMARY_LEN = 20

# --- ゲート判定の結果コード -------------------------------------------------- #
GATE_ELIGIBLE = "eligible"
GATE_NEEDS_REVIEW = "needs_review"
GATE_NOT_ELIGIBLE = "not_eligible"

# --- 除外カテゴリ（原則除外または保留） -------------------------------------- #
# 物理商品でない / 一般消費者向け物販でない企画。
# 単独で「非物理」と判断してよい語（物理商品の説明にはまず出ない）。
_NON_PHYSICAL_STRONG = (
    "mobile app", "saas", "software only", "web service", "subscription service",
    "ソフトウェア", "サブスクリプション",
    "documentary", "short film", "video game", "board game", "tabletop",
    "concert", "festival", "exhibition",
    "donation", "charity", "fundraiser", "nonprofit", "ngo", "scholarship",
    "membership", "coaching", "consulting", "retreat", "workshop",
    "映画", "書籍", "寄付", "募金", "講座",
)
# 物理商品の**付随機能・用途**としても出る語。単独では非物理と断定できず、
# 物理商品を示す語が無い場合にだけ非物理とみなす。
# 例: "companion app"（ヘッドホン）、"music player"、"gaming headset"、"bookshelf speaker"
_NON_PHYSICAL_WEAK = (
    "app", "plugin", "movie", "film", "game", "book", "novel", "comic", "manga",
    "album", "music", "song", "event", "course",
    # 日本語・韓国語の「アプリ」も付随機能として頻出するため WEAK に置く。
    # 例: 「スマート水耕栽培キット（アプリ連動）」は物理商品。
    "アプリ", "ゲーム", "音楽", "イベント",
    "앱", "게임", "음악",
)
# 物理商品であることを示す語（_NON_PHYSICAL_WEAK の打ち消しに使う）。
_PHYSICAL_PRODUCT_HINTS = (
    "headphone", "headset", "earbud", "earphone", "speaker", "soundbar",
    "watch", "camera", "lens", "lamp", "flashlight", "lantern", "projector",
    "bottle", "mug", "tumbler", "cookware", "grill", "cooler", "kettle",
    "bag", "backpack", "wallet", "case", "pouch", "luggage",
    "knife", "multitool", "screwdriver", "wrench", "toolkit",
    "charger", "power bank", "powerbank", "battery", "cable", "adapter",
    "keyboard", "mouse", "monitor", "printer", "scanner", "drone", "robot",
    "ring", "bracelet", "pendant", "glasses", "helmet", "shoe", "jacket",
    "chair", "desk", "mat", "pillow", "blanket",
    "vacuum", "purifier", "humidifier", "fan", "heater", "massager",
    "razor", "toothbrush", "trimmer", "brush",
    "tracker", "sensor", "telescope", "binocular", "microphone", "turntable",
    "device", "gadget", "hardware", "wearable", "stainless", "aluminum",
    "titanium", "waterproof", "rechargeable", "bluetooth",
    "ヘッドホン", "イヤホン", "スピーカー", "時計", "カメラ", "ライト",
    "ボトル", "バッグ", "充電", "電池", "キーボード", "財布", "ナイフ",
    # 日本語の物理商品語（形状・素材・機構）。
    "キット", "本体", "充電式", "ステンレス", "アルミ", "チタン", "防水",
    "栽培", "収納", "照明", "空気清浄", "加湿", "掃除機", "調理", "食器",
    "リュック", "工具", "ランプ", "扇風機", "マット", "椅子", "机",
    # 韓国語の物理商品語。
    "키트", "본체", "충전식", "충전", "방수", "스테인리스", "알루미늄",
    "이어폰", "헤드폰", "스피커", "카메라", "가방", "조명", "청소기",
    # 「付随アプリ」を示す複合語は、アプリが操作する**実体がある**ことの証拠。
    # 単独の "アプリ" では物理商品を除外しないための打ち消し語。
    "アプリ連動", "アプリ対応", "アプリ操作", "専用アプリ", "アプリ制御",
    "companion app", "app-enabled", "app control", "app-controlled",
    "앱 연동", "전용 앱", "앱 제어", "앱연동",
)
# 後方互換：既存の参照が壊れないよう全語を残す。
_NON_PHYSICAL_HINTS = _NON_PHYSICAL_STRONG + _NON_PHYSICAL_WEAK
# B2B 専用（一般消費者向けでない）。
_B2B_HINTS = (
    "b2b", "enterprise only", "for businesses only", "wholesale only",
    "industrial equipment", "oem only", "法人向け", "業務用専用",
)
# 大型・重量物（輸入・物流負担が大きい）。
_BULKY_HINTS = (
    "furniture set", "sofa", "mattress", "e-bike", "ebike", "electric bike",
    "scooter", "vehicle", "kayak", "tent house", "shed", "refrigerator",
    "washing machine", "大型家具", "電動自転車", "冷蔵庫", "洗濯機",
)
# 医療効果・治療効果を強くうたう商品（薬機法リスク）。
_MEDICAL_CLAIM_HINTS = (
    "cure", "treat disease", "clinically proven", "medical device",
    "therapy device", "diagnos", "治療", "医療機器", "効能",
)
# 武器・危険物。
_DANGEROUS_HINTS = (
    "weapon", "firearm", "gun ", "taser", "pepper spray", "explosive",
    "knife set", "武器", "銃", "火薬",
)
# 輸入規制負担が大きいカテゴリ（discovery_scoring の caution と揃える）。
_HEAVY_REGULATION = ("medical", "supplement", "food", "cosmetics", "nicotine", "alcohol",
                     "chemical", "weapon", "knife")

# 日本での訴求点（問題解決 / 利便性 / デザイン性 / 新規性）を示す語。
_APPEAL_HINTS: dict[str, tuple[str, ...]] = {
    "問題解決": ("solve", "problem", "pain point", "fix ", "prevent", "解決", "悩み"),
    "利便性": ("convenient", "easy", "portable", "compact", "time-saving", "one-touch",
               "便利", "手軽", "時短", "軽量"),
    "デザイン性": ("design", "minimal", "elegant", "aesthetic", "stylish",
                   "デザイン", "おしゃれ"),
    "新規性": ("world's first", "first ever", "innovative", "patented", "new type",
               "世界初", "新開発", "特許"),
}


def _has_term(text: str, term: str) -> bool:
    """語が含まれるかを判定する。

    ラテン文字を含む語は**単語境界**で照合する（"app" が "companion app" には
    一致し、"application" や "happy" には一致しない）。日本語など単語境界の概念が
    無い語は従来どおり部分一致で照合する。
    """
    if not term:
        return False
    if any("a" <= ch <= "z" for ch in term):
        return re.search(
            rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text
        ) is not None
    return term in text


def is_non_physical(text: str) -> bool:
    """テキストが「物理商品でない企画」を示すかを返す。

    - STRONG 語が1つでもあれば非物理
    - WEAK 語しかない場合は、物理商品を示す語が**無い**ときにのみ非物理
      （"companion app" を持つヘッドホンを非物理と誤判定しないため）
    """
    if any(_has_term(text, h) for h in _NON_PHYSICAL_STRONG):
        return True
    if any(_has_term(text, h) for h in _NON_PHYSICAL_WEAK):
        return not any(_has_term(text, h) for h in _PHYSICAL_PRODUCT_HINTS)
    return False


def _text_of(project: Project) -> str:
    return " ".join(
        str(x or "")
        for x in (
            project.title,
            project.description_clean or project.description,
            project.category,
        )
    ).lower()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def japan_crowdfunding_score(db: Session, project: Project) -> tuple[int | None, list[str]]:
    """日本クラファン適性スコア（既存 sales_assessment の makuake_fit）を返す。

    保存済みアセスメントがあればそれを使い、無ければ **保存せずに** その場で算出する
    （ゲート判定のためにランキング用スコアを書き換えない）。
    """
    from app.services import sales_assessment_service as sas

    latest = sas.get_latest(db, project.id)
    if latest is not None and latest.makuake_fit_score is not None:
        details = (latest.details_json or {}).get("makuake_fit") or {}
        return int(latest.makuake_fit_score), list(details.get("reasons") or [])

    try:
        sig = sas._gather_signals(db, project)
        mk = sas.score_makuake_fit(sig)
        return int(mk["score"]), list(mk.get("reasons") or [])
    except Exception as exc:  # noqa: BLE001  スコア算出失敗はゲートを止めない
        logger.warning("japan fit score failed (project=%s): %s", project.id, exc)
        return None, []


def _excluded_categories(text: str) -> list[str]:
    """原則除外・保留に該当する理由のリスト（該当なしなら空）。"""
    out: list[str] = []
    if is_non_physical(text):
        out.append("物理商品ではない企画（アプリ/映画/ゲーム/書籍/音楽/イベント/寄付 等）の可能性")
    if any(h in text for h in _B2B_HINTS):
        out.append("B2B 専用品の可能性（一般消費者向けではない）")
    if any(h in text for h in _BULKY_HINTS):
        out.append("大型・重量物で輸入/物流の負担が大きい")
    if any(h in text for h in _MEDICAL_CLAIM_HINTS):
        out.append("医療効果・治療効果を強くうたう商品（薬機法リスク）")
    if any(h in text for h in _DANGEROUS_HINTS):
        out.append("武器・危険物に該当する可能性")
    heavy = [c for c in match_categories(text, CAUTION_KEYWORDS) if c in _HEAVY_REGULATION]
    if heavy:
        out.append(f"輸入規制負担が大きいカテゴリ: {', '.join(heavy)}")
    return out


def _appeal_points(text: str) -> list[str]:
    """日本での訴求点（問題解決/利便性/デザイン性/新規性）。"""
    points = [name for name, kws in _APPEAL_HINTS.items() if any(k in text for k in kws)]
    if not points and match_categories(text, HIGH_FIT_KEYWORDS):
        # 日本クラファンで受けやすい物販カテゴリに該当すれば「利便性」を訴求点とみなす。
        points = ["利便性"]
    return points


def evaluate(db: Session, project: Project, *, persist: bool = True) -> dict:
    """ゲートを判定する。persist=True なら判定結果を projects へ保存する。

    Returns:
        eligible_for_contact_search / contact_search_gate_reason /
        japan_crowdfunding_score / gate_checked_at / reasons / rationale
    """
    text = _text_of(project)
    reasons: list[str] = []
    blockers: list[str] = []

    # 1. campaign_url
    url_state = campaign_url_mod.url_state(project)
    if url_state["campaign_url_missing"]:
        blockers.append(
            f"商品ページURL未確認（{url_state['campaign_url_missing_reason']}）"
        )
    else:
        reasons.append("商品ページURLを確認済み")

    # 2. 商品内容（日本語概要）
    from app.services import product_context_service as pcs

    summary = pcs.build_japanese_summary(project)
    if not summary or len(summary) < MIN_SUMMARY_LEN:
        blockers.append("商品内容が判別できない（日本語概要を生成できない）")
    else:
        reasons.append("日本語の商品概要あり")

    # 3. 日本クラファン適性スコア（既存 makuake_fit を再利用）
    score, score_reasons = japan_crowdfunding_score(db, project)
    reasons.extend(score_reasons[:3])

    # 4. 除外カテゴリ
    excluded = _excluded_categories(text)
    blockers.extend(excluded)

    # 5. 日本での訴求点
    appeals = _appeal_points(text)
    if appeals:
        reasons.append("日本での訴求点: " + " / ".join(appeals))
    else:
        blockers.append("日本での訴求点（問題解決/利便性/デザイン性/新規性）を説明できない")

    # --- ユーザーに見せる理由（スコアを出さず、確認可能な事実・ルールで説明する） ---
    user_reasons: list[str] = list(blockers)

    # --- 判定 ---
    if blockers:
        # 商品ページ URL・商品内容の欠落は「要確認」ではなく明確に対象外にする
        hard = any(b.startswith("商品ページURL未確認") or b.startswith("商品内容が判別できない")
                   for b in blockers)
        decision = GATE_NOT_ELIGIBLE if hard else GATE_NEEDS_REVIEW
        gate_reason = "; ".join(blockers)
    elif score is None:
        decision = GATE_NEEDS_REVIEW
        gate_reason = "日本クラファン適性スコアを算出できない（要確認）"
        user_reasons.append("商品情報が不足しており判定できないため要確認")
    elif score >= JAPAN_CF_SCORE_THRESHOLD:
        decision = GATE_ELIGIBLE
        gate_reason = f"日本クラファン適性 {score} が基準 {JAPAN_CF_SCORE_THRESHOLD} 以上"
    elif score >= JAPAN_CF_SCORE_REVIEW_FLOOR:
        decision = GATE_NEEDS_REVIEW
        gate_reason = (
            f"日本クラファン適性 {score} が基準 {JAPAN_CF_SCORE_THRESHOLD} 未満（要確認）"
        )
        user_reasons.append(
            "日本のクラウドファンディング向けの商品性を確認できないため要確認"
        )
    else:
        decision = GATE_NOT_ELIGIBLE
        gate_reason = (
            f"日本クラファン適性 {score} が下限 {JAPAN_CF_SCORE_REVIEW_FLOOR} 未満"
        )
        user_reasons.append(
            "日本のクラウドファンディング向けの商品性を確認できない"
        )

    eligible = decision == GATE_ELIGIBLE
    checked_at = _now()
    result = {
        "eligible_for_contact_search": eligible,
        "contact_search_gate_decision": decision,
        # gate_reason は内部ログ・監査用（スコアを含む）。画面には user_reasons を使う。
        "contact_search_gate_reason": gate_reason,
        # ユーザーに見せる「探索しなかった具体的理由」。スコアは含めない。
        "user_reasons": user_reasons,
        "japan_crowdfunding_score": score,
        "japan_crowdfunding_threshold": JAPAN_CF_SCORE_THRESHOLD,
        "gate_checked_at": checked_at,
        "reasons": reasons,
        "blockers": blockers,
        "rationale": (
            gate_reason if not eligible
            else "商品ページ・商品内容・日本クラファン適性の条件を満たすためメール探索を実行"
        ),
        **url_state,
    }

    if persist:
        try:
            project.eligible_for_contact_search = eligible
            project.contact_search_gate_reason = gate_reason
            project.japan_crowdfunding_score = score
            project.gate_checked_at = checked_at
            db.commit()
        except Exception as exc:  # noqa: BLE001  保存失敗でも判定結果は返す
            logger.warning("gate persist failed (project=%s): %s", project.id, exc)
            db.rollback()

    if not eligible:
        logger.info(
            "contact search gate blocked: project=%s decision=%s reason=%s",
            project.id, decision, gate_reason,
        )
    return result


class GateBlocked(Exception):
    """ゲート不合格でメール探索を開始できない。"""

    def __init__(self, result: dict):
        self.result = result
        super().__init__(result.get("contact_search_gate_reason") or "gate blocked")


def require_eligible(
    db: Session, project: Project, *, override_reason: str | None = None
) -> dict:
    """メール探索の開始可否をサーバー側で再判定する。

    override_reason（管理者による手動実行の理由）が与えられた場合は不合格でも通すが、
    override の事実と理由を必ず結果に残す。
    """
    result = evaluate(db, project)
    reason = (override_reason or "").strip()
    if result["eligible_for_contact_search"]:
        result["override"] = False
        result["override_reason"] = None
        return result
    if reason:
        result["override"] = True
        result["override_reason"] = reason
        logger.info(
            "contact search gate overridden: project=%s reason=%s gate=%s",
            project.id, reason, result["contact_search_gate_reason"],
        )
        return result
    raise GateBlocked(result)
