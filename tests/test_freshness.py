from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from solar_alerts.config import ConfigError, Settings
from solar_alerts.main import run_once
from solar_alerts.models import ArticleContent, Story
from solar_alerts.scraper import parse_article
from solar_alerts.state import StateStore


NOW = datetime(2026, 11, 11, 23, 0, tzinfo=timezone.utc)


def story(number, published_at=None):
    return Story(
        f"Story {number}",
        f"https://economictimes.indiatimes.com/industry/renewables/story/articleshow/{number}.cms",
        published_at=published_at,
    )


@pytest.fixture
def poll(monkeypatch, tmp_path):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr("solar_alerts.main.datetime", Clock)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "missing.env"))
    settings = Settings.from_env(tmp_path)
    state = StateStore(settings.state_file)
    state.initialize([])
    state.save()
    sent = []
    summarized = []
    fetched = []

    def fetch(scraper, item):
        fetched.append(item.key)
        return ArticleContent(item.title, "", "Article body")

    def summarize(summarizer, item, article):
        summarized.append(item.key)
        return "Summary"

    monkeypatch.setattr("solar_alerts.main.EconomicTimesScraper.fetch_article", fetch)
    monkeypatch.setattr("solar_alerts.main.OpenAISummarizer.__post_init__", lambda self: None)
    monkeypatch.setattr("solar_alerts.main.OpenAISummarizer.summarize", summarize)
    monkeypatch.setattr("solar_alerts.main.GmailMailer.send_alert", lambda self, item, summary: sent.append(item))

    def run(items, **kwargs):
        monkeypatch.setattr("solar_alerts.main.EconomicTimesScraper.fetch_stories", lambda self: items)
        return run_once(settings, **kwargs)

    return settings, run, sent, summarized, fetched


def test_archive_stories_are_filtered_before_delivery_limit(poll):
    settings, run, sent, summarized, fetched = poll
    old = story(128156163, datetime(2026, 2, 10, 11, 21, tzinfo=timezone.utc))
    fresh = story(135747686, NOW - timedelta(minutes=5))
    result = run([old, fresh], limit=1)
    assert [item.key for item in sent] == ["articleshow:135747686"]
    assert summarized == ["articleshow:135747686"]
    assert fetched == ["articleshow:135747686"]


def test_only_archive_stories_do_not_call_openai_or_smtp(poll):
    settings, run, sent, summarized, fetched = poll
    result = run([story(1, NOW - timedelta(days=200))])
    assert (result.sent, result.failed) == (0, 0)
    assert not sent and not summarized and not fetched


def test_age_boundary_and_future_timestamps(poll):
    settings, run, sent, summarized, fetched = poll
    run([
        story(1, NOW - timedelta(hours=24, seconds=1)),
        story(2, NOW - timedelta(hours=24)),
        story(3, NOW + timedelta(days=1)),
    ])
    assert [item.key for item in sent] == ["articleshow:2"]


def test_unknown_publication_time_is_deferred_without_marking_seen(poll):
    settings, run, sent, summarized, fetched = poll
    result = run([story(1)])
    assert result.sent == 0 and not summarized and not sent
    assert not StateStore.load(settings.state_file).has_seen("articleshow:1")


def test_undated_article_uses_original_publication_and_ignores_recent_update(poll, monkeypatch):
    settings, run, sent, summarized, fetched = poll
    article = parse_article('''
        <h1 class="artTitle">Archive</h1><div class="artText">Body</div>
        <script type="application/ld+json">{
          "@type": "NewsArticle", "datePublished": "2026-02-10T16:51:00+05:30",
          "dateModified": "2026-11-11T23:00:00Z"
        }</script>
    ''')
    monkeypatch.setattr("solar_alerts.main.EconomicTimesScraper.fetch_article", lambda self, item: article)
    # A recent listing timestamp must not turn a republished archive into news.
    result = run([story(1, NOW - timedelta(minutes=1)), story(2)])
    assert result.sent == 0 and not sent and not summarized


def test_undated_fresh_article_gets_verified_time_in_email(poll, monkeypatch):
    settings, run, sent, summarized, fetched = poll
    article = parse_article('''
        <h1 class="artTitle">Fresh</h1><div class="artText">Body</div>
        <script type="application/ld+json">{
          "@graph": [{"@type": ["Article", "NewsArticle"],
                      "datePublished": "2026-11-12T04:25:00+05:30"}]
        }</script>
    ''')
    monkeypatch.setattr("solar_alerts.main.EconomicTimesScraper.fetch_article", lambda self, item: article)
    run([story(1)])
    assert len(sent) == 1
    assert sent[0].published_at == datetime(2026, 11, 11, 22, 55, tzinfo=timezone.utc)


def test_failed_delivery_remains_retryable(poll, monkeypatch):
    settings, run, sent, summarized, fetched = poll

    def fail(*args):
        raise OSError("SMTP unavailable")

    monkeypatch.setattr("solar_alerts.main.GmailMailer.send_alert", fail)
    result = run([story(1, NOW - timedelta(minutes=2))])
    assert result.failed == 1
    assert not StateStore.load(settings.state_file).has_seen("articleshow:1")


def test_stale_undated_article_is_not_fetched_again(poll, monkeypatch):
    settings, run, sent, summarized, fetched = poll
    article = parse_article('''
        <h1 class="artTitle">Archive</h1><div class="artText">Body</div>
        <meta property="article:published_time" content="2026-02-10T11:21:00Z">
    ''')

    def fetch(scraper, item):
        fetched.append(item.key)
        return article

    monkeypatch.setattr("solar_alerts.main.EconomicTimesScraper.fetch_article", fetch)
    run([story(1)])
    run([story(1)])
    assert fetched == ["articleshow:1"]
    assert not StateStore.load(settings.state_file).has_seen("articleshow:1")
    assert not sent


def test_dry_run_explains_freshness_without_state_changes(poll, capsys):
    settings, run, sent, summarized, fetched = poll
    before = settings.state_file.read_bytes()
    run([story(1, NOW - timedelta(days=200)), story(2)], dry_run=True)
    output = capsys.readouterr().out
    assert "stale" in output.lower() and "unverified" in output.lower()
    assert settings.state_file.read_bytes() == before
    assert not sent and not summarized and not fetched


def test_invalid_freshness_setting_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("MAX_STORY_AGE_HOURS", "0")
    with pytest.raises(ConfigError, match="MAX_STORY_AGE_HOURS"):
        Settings.from_env(tmp_path).validate(require_email=False, require_openai=False)
