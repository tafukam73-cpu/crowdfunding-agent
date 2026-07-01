"""アプリ全体の設定。環境変数（.env）から読み込む。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # PostgreSQL 接続情報
    database_url: str = (
        "postgresql+psycopg://cfagent:cfagent_password@db:5432/crowdfunding"
    )

    # アプリ表示名
    app_name: str = "海外クラファン案件発掘・営業支援システム"

    # CORS 許可オリジン（フロントエンド）
    frontend_origin: str = "http://localhost:3000"

    # スクレイピング取得方法： "httpx" | "playwright"
    scrape_fetcher: str = "playwright"
    # リクエスト間隔（秒）。レート制限への配慮
    scrape_rate_limit_seconds: float = 2.0
    # 取得タイムアウト（秒）
    scrape_timeout_seconds: float = 30.0
    # リトライ回数（403/タイムアウト/5xx 時。UA をローテーションして再試行）
    scrape_retries: int = 2

    # AI 評価：未設定ならモック評価器が使われる
    anthropic_api_key: str = ""
    # 既定は最も高性能な Opus 4.8。コスト重視なら claude-sonnet-4-6 等に変更可
    anthropic_model: str = "claude-opus-4-8"

    # 営業メールの差出人（署名に使用）
    sender_name: str = "Taro Yamada"
    sender_company: str = "Your Company"

    # --- メール下書きプロバイダー（Gmail 等。未設定なら mock） ---
    # Gmail API（OAuth2 リフレッシュトークン方式）。3 つ揃うと Gmail を使用。
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_refresh_token: str = ""
    # 下書きを作成する Gmail ユーザー（通常 "me"）と From 表示（省略可）
    gmail_user: str = "me"
    gmail_sender: str = ""

    # --- 日次自動収集スケジューラ ---
    # 有効化フラグ（false で起動時にスケジューラを開始しない）
    scrape_schedule_enabled: bool = True
    # 実行スケジュール（cron 式）。既定は毎日 03:00。
    scrape_schedule_cron: str = "0 3 * * *"
    # スケジュールのタイムゾーン
    scrape_timezone: str = "Asia/Tokyo"
    # 自動収集の 1 サイトあたり取得上限
    scrape_daily_limit: int = 20

    # --- Web Research 検索プロバイダー ---
    # 検索 API の切り替え：
    #   "none" | "brave" | "serpapi" | "tavily" | "google_cse" | "bing" | "multi"
    # "multi" は設定済みのプロバイダーを順に試し、最初に結果が返ったものを使う。
    # API キーが無い場合は自動的に DuckDuckGo HTML（手動検索クエリ方式）にフォールバック。
    search_provider: str = "none"
    # 各プロバイダーの API キー（設定したものだけ使用可能になる）
    brave_search_api_key: str = ""
    serpapi_api_key: str = ""
    tavily_api_key: str = ""
    google_cse_api_key: str = ""
    # Google Custom Search のエンジン ID（cx）。google_cse には key と cx の両方が必要。
    google_cse_cx: str = ""
    # Bing Web Search API（Azure）
    bing_search_api_key: str = ""
    # 1 クエリあたり取得する検索結果の上限
    search_max_results: int = 10

    # --- AI Search Agent の探索上限（発見率と安全性のバランス） ---
    search_agent_max_steps: int = 15
    search_agent_max_urls: int = 40
    search_agent_max_queries: int = 50
    # 同一ドメインを過剰巡回しない上限
    search_agent_max_per_domain: int = 8

    # --- 取得アラート通知（構造変化・成功率低下） ---
    # Slack Incoming Webhook URL。設定時のみ Slack 通知を行う（未設定なら何もしない）。
    slack_webhook_url: str = ""
    # 管理画面のベース URL（通知本文のリンク用）。未設定なら frontend_origin を使う。
    app_base_url: str = ""
    # 監視集計の対象にする直近 run 件数（通知判定にも使う）
    alert_window: int = 20

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
