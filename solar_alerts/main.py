from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import logging
import sys
import time

from .config import ConfigError, Settings
from .mailer import GmailMailer
from .scraper import EconomicTimesScraper, ScraperError
from .state import StateError, StateStore
from .summarizer import OpenAISummarizer


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunResult:
    discovered: int
    candidates: int
    sent: int
    failed: int
    baselined: bool = False
    skipped: int = 0
    deferred: int = 0


def _freshness(published_at: datetime | None, now: datetime, max_age_hours: int) -> str:
    if published_at is None or published_at.tzinfo is None:
        return "unverified"
    if published_at > now + timedelta(minutes=5):
        return "future"
    if published_at < now - timedelta(hours=max_age_hours):
        return "stale"
    return "recent"


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
        additional_page_urls=(settings.source_power_page_url, settings.source_renewables_page_url, settings.source_infrastructure_page_url, settings.source_policy_page_url),
    )
    stories = scraper.fetch_stories()
    if not stories:
        raise ScraperError("No stories were discovered")
    now = datetime.now(timezone.utc)

    if dry_run:
        print(f"Discovered {len(stories)} in-scope stories.")
        for story in stories[:limit]:
            when = story.published_at.isoformat() if story.published_at else "unknown time"
            status = _freshness(story.published_at, now, settings.max_story_age_hours)
            print(f"- {status} | {when} | {story.title} | {story.canonical_url}")
        return RunResult(discovered=len(stories), candidates=0, sent=0, failed=0)

    state = StateStore.load(settings.state_file)
    if not state.initialized and not notify_current:
        state.initialize([story.key for story in stories])
        state.save()
        LOGGER.info("Baselined %d current stories; no initial emails sent", len(stories))
        return RunResult(discovered=len(stories), candidates=0, sent=0, failed=0, baselined=True)
    if not state.initialized:
        state.initialize([])
        state.save()

    candidates = []
    skipped = 0
    deferred = 0
    for story in stories:
        if state.has_processed(story.key):
            continue
        status = _freshness(story.published_at, now, settings.max_story_age_hours)
        if status == "stale":
            state.mark_stale(story.key)
            skipped += 1
            LOGGER.info("Skipped stale %s published=%s", story.key, story.published_at.isoformat())
        elif status == "future":
            deferred += 1
            LOGGER.warning("Deferred future-dated %s published=%s", story.key, story.published_at.isoformat())
        else:
            candidates.append(story)
    if skipped:
        state.save()
    candidates.sort(
        key=lambda story: story.published_at if story.published_at is not None and story.published_at.tzinfo is not None else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    # Within a bounded batch, new/least recently attempted links take priority
    # over retries so unavailable metadata cannot monopolize every poll.
    candidates.sort(key=lambda story: state.last_attempted.get(story.key, ""))
    candidates = candidates[: min(settings.max_articles_per_run, limit or settings.max_articles_per_run)]
    LOGGER.info(
        "Discovery: total=%d candidates=%d skipped_stale=%d deferred=%d max_age_hours=%d",
        len(stories), len(candidates), skipped, deferred, settings.max_story_age_hours,
    )
    if not candidates:
        LOGGER.info("No new stories")
        return RunResult(discovered=len(stories), candidates=0, sent=0, failed=0, skipped=skipped, deferred=deferred)

    summarizer = None
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
            state.record_attempt(story.key)
            state.save()
            article = scraper.fetch_article(story)
            # Listings may display a modification time. Original publication
            # metadata takes precedence, including when a story is undated.
            story = replace(story, published_at=article.published_at or (story.published_at if story.source == "rss" else None))
            status = _freshness(story.published_at, datetime.now(timezone.utc), settings.max_story_age_hours)
            if status == "stale":
                state.mark_stale(story.key)
                state.save()
                skipped += 1
                LOGGER.info("Skipped stale %s after article verification published=%s", story.key, story.published_at.isoformat())
                continue
            if status in {"unverified", "future"}:
                deferred += 1
                LOGGER.warning("Deferred %s: publication time %s", story.key, status)
                continue
            if (article.body or article.summary or story.excerpt).strip():
                if summarizer is None:
                    summarizer = OpenAISummarizer(
                        api_key=settings.openai_api_key,
                        model=settings.openai_model,
                        reasoning_effort=settings.openai_reasoning_effort,
                        max_input_chars=settings.article_max_chars,
                    )
                summary = summarizer.summarize(story, article)
            else:
                summary = "Article text and summary are unavailable. Open the article link for details."
            mailer.send_alert(story, summary)
        except StateError:
            raise
        except Exception as exc:
            failed += 1
            LOGGER.exception("Could not process %s: %s", story.key, exc)
            continue
        state.mark_seen(story.key)
        state.save()
        sent += 1
        LOGGER.info("Sent alert for %s published=%s", story.title, story.published_at.isoformat())
    LOGGER.info("Poll complete: sent=%d failed=%d skipped_stale=%d deferred=%d", sent, failed, skipped, deferred)
    return RunResult(discovered=len(stories), candidates=len(candidates), sent=sent, failed=failed, skipped=skipped, deferred=deferred)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Email AI summaries of new Economic Times energy, infrastructure, and policy stories.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="Keep polling instead of running once")
    mode.add_argument("--once", action="store_true", help="Run one poll (the default)")
    parser.add_argument("--interval", type=int, help="Override POLL_INTERVAL_SECONDS in watch mode")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and print stories without OpenAI, email, or state changes")
    parser.add_argument("--notify-current", action="store_true", help="Send eligible recent stories on an uninitialized state file")
    parser.add_argument("--limit", type=int, help="Cap alerts after freshness filtering, or listings printed in a dry run")
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
    LOGGER.info(
        "Watching %s, %s, %s, %s, and %s every %d seconds",
        settings.source_page_url,
        settings.source_power_page_url,
        settings.source_renewables_page_url,
        settings.source_infrastructure_page_url,
        settings.source_policy_page_url,
        interval,
    )
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
