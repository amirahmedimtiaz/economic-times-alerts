from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when the alert service is missing required configuration."""


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _recipients(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.replace(";", ",").split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    source_page_url: str
    source_rss_url: str
    request_timeout_seconds: int
    poll_interval_seconds: int
    max_articles_per_run: int
    article_max_chars: int
    state_file: Path
    email_sender: str
    email_password: str
    email_receivers: tuple[str, ...]
    email_subject_prefix: str
    email_smtp_host: str
    email_smtp_port: int
    email_smtp_ssl: bool
    openai_api_key: str
    openai_model: str
    openai_reasoning_effort: str

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "Settings":
        root = (base_dir or Path.cwd()).resolve()
        env_file = Path(os.getenv("ENV_FILE", str(root / ".env"))).expanduser()
        load_dotenv(env_file, override=False)

        state_value = os.getenv("STATE_FILE", "state.json")
        state_file = Path(state_value).expanduser()
        if not state_file.is_absolute():
            state_file = root / state_file

        return cls(
            source_page_url=os.getenv(
                "SOURCE_PAGE_URL",
                "https://economictimes.indiatimes.com/industry/renewables/solar-energy",
            ).split("#", 1)[0],
            source_rss_url=os.getenv(
                "SOURCE_RSS_URL",
                "https://economictimes.indiatimes.com/rssfeeds/cfmid-4005094.cms",
            ),
            request_timeout_seconds=_env_int("REQUEST_TIMEOUT_SECONDS", 30),
            poll_interval_seconds=_env_int("POLL_INTERVAL_SECONDS", 900),
            max_articles_per_run=_env_int("MAX_ARTICLES_PER_RUN", 10),
            article_max_chars=_env_int("ARTICLE_MAX_CHARS", 18_000),
            state_file=state_file.resolve(),
            email_sender=os.getenv("EMAIL_SENDER", "").strip(),
            email_password=os.getenv("EMAIL_PASSWORD", ""),
            email_receivers=_recipients(os.getenv("EMAIL_RECEIVER", "")),
            email_subject_prefix=os.getenv("EMAIL_SUBJECT_PREFIX", "[ET Solar]").strip(),
            email_smtp_host=os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com").strip(),
            email_smtp_port=_env_int("EMAIL_SMTP_PORT", 465),
            email_smtp_ssl=_env_bool("EMAIL_SMTP_SSL", True),
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-5.6-luna").strip(),
            openai_reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "max").strip().lower(),
        )

    def validate(self, *, require_email: bool = True, require_openai: bool = True) -> None:
        errors: list[str] = []
        if self.request_timeout_seconds <= 0:
            errors.append("REQUEST_TIMEOUT_SECONDS must be greater than zero")
        if self.poll_interval_seconds <= 0:
            errors.append("POLL_INTERVAL_SECONDS must be greater than zero")
        if self.max_articles_per_run <= 0:
            errors.append("MAX_ARTICLES_PER_RUN must be greater than zero")
        if self.article_max_chars < 500:
            errors.append("ARTICLE_MAX_CHARS must be at least 500")
        if require_email:
            if not self.email_sender:
                errors.append("EMAIL_SENDER is required")
            if not self.email_password:
                errors.append("EMAIL_PASSWORD is required")
            if not self.email_receivers:
                errors.append("EMAIL_RECEIVER is required")
        if require_openai and not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required for AI summarization")
        if self.openai_reasoning_effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
            errors.append("OPENAI_REASONING_EFFORT must be one of none, low, medium, high, xhigh, max")
        if errors:
            raise ConfigError("Configuration errors:\n- " + "\n- ".join(errors))
