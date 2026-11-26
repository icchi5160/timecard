import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    database_path: str = "./data/timecard.db"
    bot_token: str = ""
    app_token: str = ""
    channel_id: str = ""
    team_id: str = ""
    interval: int = 360
    timeout: int = 30
    retention_days: int = 90
    timezone: str = "Asia/Tokyo"
    log_level: str = "INFO"

    @classmethod
    def load(cls):
        load_dotenv()
        config = cls(
            database_path=os.getenv("DATABASE_PATH", "./data/timecard.db"),
            bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
            app_token=os.getenv("SLACK_APP_TOKEN", ""),
            channel_id=os.getenv("SLACK_CHANNEL_ID", ""),
            team_id=os.getenv("SLACK_TEAM_ID", ""),
            interval=int(os.getenv("CONFIRMATION_INTERVAL_MINUTES", "360")),
            timeout=int(os.getenv("CONFIRMATION_TIMEOUT_MINUTES", "30")),
            retention_days=int(os.getenv("RETENTION_DAYS", "90")),
            timezone=os.getenv("TIMEZONE", "Asia/Tokyo"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )
        ZoneInfo(config.timezone)
        if min(config.interval, config.timeout, config.retention_days) <= 0:
            raise ValueError("Intervals and retention must be positive")
        if config.timezone != "Asia/Tokyo":
            raise ValueError("v1 requires TIMEZONE=Asia/Tokyo")
        return config

    def validate_slack(self):
        if not (
            self.bot_token.startswith("xoxb-")
            and self.app_token.startswith("xapp-")
            and self.channel_id
            and self.team_id
        ):
            raise ValueError("Slack tokens, channel ID and team ID are required")
