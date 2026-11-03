from datetime import datetime, timezone
from dataclasses import replace

from solar_alerts.config import Settings
from solar_alerts.main import run_once
from solar_alerts.models import ArticleContent, Story
from solar_alerts.state import StateStore


def test_newest_dated_story_is_sent_before_undated_backlog(monkeypatch, tmp_path):
    monkeypatch.setenv('ENV_FILE', str(tmp_path / 'absent'))
    settings = replace(Settings.from_env(tmp_path), max_articles_per_run=1)
    state = StateStore.load(settings.state_file)
    state.initialize(['articleshow:9'])
    state.save()
    older = Story('Undated', 'https://economictimes.indiatimes.com/industry/renewables/x/articleshow/1.cms')
    latest = Story('New', 'https://economictimes.indiatimes.com/industry/renewables/y/articleshow/2.cms', published_at=datetime(2026, 11, 3, tzinfo=timezone.utc))
    monkeypatch.setattr('solar_alerts.main.EconomicTimesScraper.fetch_stories', lambda self: [latest, older])
    monkeypatch.setattr('solar_alerts.main.EconomicTimesScraper.fetch_article', lambda self, story: ArticleContent(story.title, '', 'body'))
    monkeypatch.setattr('solar_alerts.main.OpenAISummarizer.__post_init__', lambda self: None)
    monkeypatch.setattr('solar_alerts.main.OpenAISummarizer.summarize', lambda self, story, article: 'summary')
    sent = []
    monkeypatch.setattr('solar_alerts.main.GmailMailer.send_alert', lambda self, story, summary: sent.append(story.title))
    run_once(settings)
    assert sent == ['New']
