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


def _evaluate_gate_only(db: Session, project: Project) -> dict:
    """既存のゲート判定（makuake_fit 45/30 ＋ 除外語 ＋ 訴求点）だけを行う。

    **この関数のロジックは LQE 導入前と同一**（温存パス）。判定結果を保存せず、
    dict を返すだけ。LQE との合成は ``merge_gate_with_lqe`` が行う。
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
    return {
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


def _qualify_or_none(db: Session, project: Project):
    """LQE の判定（pre_research）を返す。実行できなければ None。

    ``lead_qualification_service`` は遅延 import する（LQE 側も非物理語彙のために
    このモジュールを遅延 import しており、module レベルだと循環するため）。

    **``run()`` は呼ばない。** 履歴は書かず、``gather_signals`` → ``qualify`` の
    読み取り＋純粋関数だけを使う（外部 HTTP なし・DB 書き込みなし）。
    LQE 側の失敗でゲート全体を止めないよう、例外は握って None を返す。
    """
    try:
        from app.services import lead_qualification_service as lqs

        signals = lqs.gather_signals(db, project)
        return lqs.qualify(signals, lqs.STAGE_PRE_RESEARCH)
    except Exception as exc:  # noqa: BLE001  LQE 失敗でゲートを止めない
        logger.warning("lead qualification failed (project=%s): %s", project.id, exc)
        return None


# --------------------------------------------------------------------------- #
#  LQE との合成（純粋関数）
# --------------------------------------------------------------------------- #
# 判定の重さ。小さいほど厳しい。合成では **決して緩和しない**（never upgrade）。
_DECISION_RANK = {GATE_NOT_ELIGIBLE: 0, GATE_NEEDS_REVIEW: 1, GATE_ELIGIBLE: 2}

# merge が付与する LQE 由来のキー。PR-3 では **既定でレスポンスに出さない**
# （API を変えないため）。公開は PR-4 の専用エンドポイントで行う。
LQE_DETAIL_FIELDS: tuple[str, ...] = (
    "lqe_decision", "lqe_blocker_codes", "lqe_review_codes",
)

_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")


def _normalize_reason(text: str) -> str:
    """理由文の重複判定用に正規化する（括弧内の補足と空白を落とす）。"""
    return re.sub(r"\s+", "", _PAREN_RE.sub("", text or ""))


def _is_duplicate_reason(candidate: str, existing_norms: list[str]) -> bool:
    """既出の理由と同じことを言っているか（部分一致を含む）。"""
    norm = _normalize_reason(candidate)
    if not norm:
        return True
    return any(norm in e or e in norm for e in existing_norms if e)


def _split_reason(text: str) -> list[str]:
    """LQE の理由は複数事実を "; " で連結していることがあるため分解する。"""
    return [part.strip() for part in (text or "").split(";") if part.strip()]


def merge_gate_with_lqe(gate_result: dict, qualification_result) -> dict:
    """既存ゲートの判定と LQE の判定を合成する（**純粋関数**）。

    DB アクセス・外部 HTTP・commit を一切行わない。入力の dict も変更せず、
    新しい dict を返す。

    合成ルール:
      - LQE ``blocked``  → ゲートを ``not_eligible`` へ**降格**し、blocker の理由を追記
      - LQE ``review``   → **decision は変えない。** 理由だけ追記する
      - LQE ``clear``    → ゲート結果をそのまま返す
      - **never upgrade**: どの場合もゲートより緩い判定にはしない

    理由は重複排除する（ゲートの「商品ページURL未確認」と LQE の T が同じ事実を
    二重に出さない）。LQE 由来のキー（LQE_DETAIL_FIELDS）の付与もこの関数の責務。

    Args:
        gate_result: ``_evaluate_gate_only`` の戻り値。
        qualification_result: ``lead_qualification_service.QualificationResult``
            または None（LQE を実行できなかった場合。その場合はゲート結果を返す）。
    """
    merged = dict(gate_result)
    if qualification_result is None:
        return merged

    lqe_decision = getattr(qualification_result, "decision", None)
    blocker_codes = list(getattr(qualification_result, "blocker_codes", []) or [])
    review_codes = list(getattr(qualification_result, "review_codes", []) or [])
    findings = list(getattr(qualification_result, "findings", []) or [])

    merged["lqe_decision"] = lqe_decision
    merged["lqe_blocker_codes"] = blocker_codes
    merged["lqe_review_codes"] = review_codes

    severities = {"blocked": "blocker", "review": "review"}
    target = severities.get(lqe_decision or "")
    if target is None:  # clear（または未知値）→ ゲート結果をそのまま
        return merged

    # 追記する理由（decision を動かした/人が見るべき Finding の理由だけ）。
    user_reasons = list(merged.get("user_reasons") or [])
    blockers = list(merged.get("blockers") or [])
    existing_norms = [_normalize_reason(r) for r in user_reasons + blockers]

    added: list[str] = []
    for finding in findings:
        if getattr(finding, "severity", None) != target:
            continue
        for part in _split_reason(getattr(finding, "reason", "")):
            if _is_duplicate_reason(part, existing_norms):
                continue
            added.append(part)
            existing_norms.append(_normalize_reason(part))

    if target == "blocker":
        # LQE が証跡付きで止めている → ゲートを not_eligible へ降格する。
        merged["contact_search_gate_decision"] = GATE_NOT_ELIGIBLE
        merged["eligible_for_contact_search"] = False
        blockers.extend(added)
        merged["blockers"] = blockers
        if added:
            merged["contact_search_gate_reason"] = "; ".join(
                [merged.get("contact_search_gate_reason") or "", *added]
            ).strip("; ")
            merged["rationale"] = merged["contact_search_gate_reason"]
    user_reasons.extend(added)
    merged["user_reasons"] = user_reasons

    # never upgrade の保証：合成後がゲート単独より緩くなっていないことを確認する。
    before = _DECISION_RANK.get(gate_result.get("contact_search_gate_decision"), 2)
    after = _DECISION_RANK.get(merged["contact_search_gate_decision"], 2)
    if after > before:
        merged["contact_search_gate_decision"] = gate_result[
            "contact_search_gate_decision"
        ]
        merged["eligible_for_contact_search"] = gate_result[
            "eligible_for_contact_search"
        ]
    return merged


def evaluate(
    db: Session,
    project: Project,
    *,
    persist: bool = True,
    include_lqe_detail: bool = False,
) -> dict:
    """ゲートを判定する。persist=True なら判定結果を projects へ保存する。

    流れ: 既存ゲート → gather_signals() → qualify() → merge_gate_with_lqe()

    LQE は判定を **厳しくする方向にだけ** 効く（never upgrade）。LQE の実行に
    失敗してもゲートは止めない（従来どおりの結果を返す）。

    **履歴（lead_qualifications）は書かない。** この関数は GET のリードパス
    （/facts・/contact-search-gate）からも呼ばれるため、``run()`` を呼ぶと画面を
    開くたびに履歴が増える。履歴保存は明示的な再判定の責務とする。

    include_lqe_detail=False（既定）のときは LQE 由来のキーを落とす。PR-3 では
    既存 API のレスポンス形を一切変えないため（公開は PR-4）。

    Returns:
        eligible_for_contact_search / contact_search_gate_reason /
        japan_crowdfunding_score / gate_checked_at / reasons / rationale
    """
    gate_result = _evaluate_gate_only(db, project)
    qualification = _qualify_or_none(db, project)
    result = merge_gate_with_lqe(gate_result, qualification)
    if not include_lqe_detail:
        for key in LQE_DETAIL_FIELDS:
            result.pop(key, None)

    decision = result["contact_search_gate_decision"]
    eligible = result["eligible_for_contact_search"]
    gate_reason = result["contact_search_gate_reason"]

    if persist:
        try:
            project.eligible_for_contact_search = eligible
            project.contact_search_gate_reason = gate_reason
            project.japan_crowdfunding_score = result["japan_crowdfunding_score"]
            project.gate_checked_at = result["gate_checked_at"]
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
