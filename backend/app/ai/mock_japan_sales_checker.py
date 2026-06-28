"""モック日本販売状況チェッカー。

外部 API・Web 検索を使わず、保守的な既定（日本での販売実績は確認できなかった）を
返す。Claude 未設定時でも画面・DB・メール連携を確認できるようにするためのもの。

「販売が確認できなかった」という既定は安全側（営業価値が高い＝★5）に倒れており、
要件の AI コメント例（初の販売代理店になれる可能性）にも沿う。実際の確認は各チャネルの
検索 URL から営業担当が手動で行う前提。
"""
from __future__ import annotations

from app.ai.japan_sales_checker import (
    CHANNEL_KEYS,
    STATUS_NOT_FOUND,
    JapanSalesChecker,
    JapanSalesResult,
)
from app.models.project import Project


class MockJapanSalesChecker(JapanSalesChecker):
    name = "mock-japan-sales-v1"

    def check(self, project: Project) -> JapanSalesResult:
        # すべて not_found（保守的な既定）。★は service 側で compute_stars により 5。
        statuses = {k: STATUS_NOT_FOUND for k in CHANNEL_KEYS}
        product = project.title or "this product"
        comment = (
            f"日本市場では「{product}」の販売実績（Amazon.co.jp / 楽天 / Yahoo! "
            "ショッピング）や日本代理店・日本法人、Makuake / GREEN FUNDING の掲載歴を"
            "自動調査では確認できませんでした。初の販売代理店になれる可能性があります。"
            "各チャネルの検索リンクから最終確認することをおすすめします。"
        )
        return JapanSalesResult(
            channel_statuses=statuses,
            channel_notes={},
            ai_comment=comment,
            summary="日本での販売・掲載は確認できませんでした（営業価値が高い）。",
            model=self.name,
        )
