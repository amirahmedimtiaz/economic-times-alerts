from solar_alerts.config import Settings
from solar_alerts.main import run_once
from solar_alerts.scraper import EconomicTimesScraper
from datetime import datetime, timezone


def test_default_poll_discovers_infrastructure_listing(monkeypatch, tmp_path, capsys):
    infrastructure_url = "https://economictimes.indiatimes.com/news/economy/infrastructure"

    class Response:
        content = b"<rss><channel></channel></rss>"
        text = '<a href="/news/economy/infrastructure/new-road/articleshow/123456.cms">New road</a>'

    def get(self, url):
        response = Response()
        if url != infrastructure_url:
            response.text = ""
        return response

    monkeypatch.setattr(EconomicTimesScraper, "_get", get)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "missing.env"))
    settings = Settings.from_env(tmp_path)
    result = run_once(settings, dry_run=True)

    assert result.discovered == 1
    assert "New road" in capsys.readouterr().out


def test_default_poll_discovers_policy_listing(monkeypatch, tmp_path, capsys):
    policy_url = "https://economictimes.indiatimes.com/news/economy/policy"

    class Response:
        content = b"<rss><channel></channel></rss>"
        text = '<a href="/news/economy/policy/new-policy/articleshow/654321.cms">New policy</a>'

    def get(self, url):
        response = Response()
        if url != policy_url:
            response.text = ""
        return response

    monkeypatch.setattr(EconomicTimesScraper, "_get", get)
    monkeypatch.setenv("ENV_FILE", str(tmp_path / "missing.env"))
    result = run_once(Settings.from_env(tmp_path), dry_run=True)

    assert result.discovered == 1
    assert "New policy" in capsys.readouterr().out


def test_unreadable_article_sends_link_only_without_summarizer(monkeypatch, tmp_path):
    from solar_alerts.models import Story, ArticleContent
    from solar_alerts.mailer import GmailMailer
    from solar_alerts.summarizer import OpenAISummarizer
    from solar_alerts.state import StateStore
    story = Story('Road', 'https://economictimes.indiatimes.com/news/economy/infrastructure/x/articleshow/1.cms', published_at=datetime.now(timezone.utc))
    monkeypatch.setattr(EconomicTimesScraper, 'fetch_stories', lambda self: [story])
    monkeypatch.setattr(EconomicTimesScraper, 'fetch_article', lambda self, story: ArticleContent(story.title, '', ''))
    monkeypatch.setattr(OpenAISummarizer, '__post_init__', lambda self: None)
    monkeypatch.setattr(OpenAISummarizer, 'summarize', lambda *args: (_ for _ in ()).throw(AssertionError('must not summarize empty text')))
    sent = []
    monkeypatch.setattr(GmailMailer, 'send_alert', lambda self, story, summary: sent.append(summary))
    monkeypatch.setenv('ENV_FILE', str(tmp_path / 'missing.env'))
    state = StateStore(tmp_path / 'state.json'); state.initialize([]); state.save()
    result = run_once(Settings.from_env(tmp_path))
    assert result.sent == 1 and result.failed == 0
    assert 'unavailable' in sent[0].lower()
    assert StateStore.load(tmp_path / 'state.json').has_seen(story.key)
