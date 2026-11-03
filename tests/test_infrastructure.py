from solar_alerts.config import Settings
from solar_alerts.main import run_once
from solar_alerts.scraper import EconomicTimesScraper


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
