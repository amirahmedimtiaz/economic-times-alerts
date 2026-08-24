from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import sys
import time

from .config import ConfigError, Settings
from .mailer import GmailMailer
from .scraper import EconomicTimesScraper, ScraperError
from .state import StateError, StateStore
from .summarizer import OpenAISummarizer, SummarizationError


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunResult:
    discovered: int
    candidates: int
    sent: int
    failed: int
    baselined: bool = False


def run_once(
    settings: Settings,
    *,
    notify_current: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
) -> RunResult:
    scraper = EconomicTimesScraper(
        page_url=settings.source_page_url,
        rss_url=settings.source_rss_url,
        timeout_seconds=settings.request_timeout_seconds,
        max_stories=max(settings.max_articles_per_run, limit or 0, 50),
    )
    stories = scraper.fetch_stories()
    if limit is not None:
        stories = stories[:limit]
    if not stories:
        raise ScraperError("No stories were discovered")

    if dry_run:
        print(f"Discovered {len(stories)} in-scope stories.")
        for story in stories:
            when = story.published_at.isoformat() if story.published_at else "unknown time"
            print(f"- {when} | {story.title} | {story.canonical_url}")
        return RunResult(discovered=len(stories), candidates=0, sent=0, failed=0)

    state = StateStore.load(settings.state_file)
    if not state.initialized and not notify_current:
        state.initialize([story.key for story in stories])
        state.save()
        LOGGER.info("Baselined %d current stories; no initial emails sent", len(stories))
        return RunResult(discovered=len(stories), candidates=0, sent=0, failed=0, baselined=True)

    candidates = [story for story in stories if not state.has_seen(story.key)]
    candidates.sort(
        key=lambda story: story.published_at or datetime.min.replace(tzinfo=timezone.utc)
    )
    candidates = candidates[: settings.max_articles_per_run]
    if not candidates:
        LOGGER.info("No new stories")
        return RunResult(discovered=len(stories), candidates=0, sent=0, failed=0)

    summarizer = OpenAISummarizer(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        reasoning_effort=settings.openai_reasoning_effort,
        max_input_chars=settings.article_max_chars,
    )
    mailer = GmailMailer(
        sender=settings.email_sender,
        password=settings.email_password,
        receivers=settings.email_receivers,
        subject_prefix=settings.email_subject_prefix,
        smtp_host=settings.email_smtp_host,
        smtp_port=settings.email_smtp_port,
        smtp_ssl=settings.email_smtp_ssl,
        timeout_seconds=settings.request_timeout_seconds,
    )

    sent = 0
    failed = 0
    for story in candidates:
        try:
            article = scraper.fetch_article(story)
            summary = summarizer.summarize(story, article)
            mailer.send_alert(story, summary)
        except Exception as exc:
            failed += 1
            LOGGER.exception("Could not process %s: %s", story.key, exc)
            continue
        state.mark_seen(story.key)
        state.save()
        sent += 1
        LOGGER.info("Sent alert for %s", story.title)
    return RunResult(discovered=len(stories), candidates=len(candidates), sent=sent, failed=failed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Email AI summaries of new Economic Times renewables stories.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="Keep polling instead of running once")
    mode.add_argument("--once", action="store_true", help="Run one poll (the default)")
    parser.add_argument("--interval", type=int, help="Override POLL_INTERVAL_SECONDS in watch mode")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print stories without OpenAI, email, or state changes")
    parser.add_argument("--notify-current", action="store_true", help="Send alerts for current stories on an uninitialized state file")
    parser.add_argument("--limit", type=int, help="Inspect at most this many discovered stories")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def _run_cli(args: argparse.Namespace) -> int:
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.from_env()
    settings.validate(require_email=not args.dry_run, require_openai=not args.dry_run)
    if args.interval is not None and args.interval <= 0:
        raise ConfigError("--interval must be greater than zero")
    if args.limit is not None and args.limit <= 0:
        raise ConfigError("--limit must be greater than zero")

    if not args.watch:
        result = run_once(settings, notify_current=args.notify_current, dry_run=args.dry_run, limit=args.limit)
        return 1 if result.failed else 0

    interval = args.interval or settings.poll_interval_seconds
    LOGGER.info("Watching %s every %d seconds", settings.source_page_url, interval)
    while True:
        try:
            result = run_once(settings, notify_current=args.notify_current, dry_run=args.dry_run, limit=args.limit)
            if result.failed:
                LOGGER.warning("The poll completed with %d failed article(s); they will be retried", result.failed)
        except (ConfigError, ScraperError, StateError, OSError) as exc:
            LOGGER.exception("Poll failed: %s", exc)
        if args.dry_run:
            return 0
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    try:
        return _run_cli(build_parser().parse_args(argv))
    except (ConfigError, ScraperError, StateError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
