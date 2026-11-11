from datetime import datetime, timezone

from solar_alerts.scraper import EconomicTimesScraper, parse_article, parse_feed, parse_page

import pytest


RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title><![CDATA[New solar story]]></title>
<link>https://economictimes.indiatimes.com/industry/renewables/new-solar-story/articleshow/12345.cms?utm_source=x</link>
<description><![CDATA[<p>Key detail.</p>]]></description>
<pubDate>Mon, 24 Aug 2026 05:30:00 +0530</pubDate></item>
<item><title>New power story</title>
<link>https://economictimes.indiatimes.com/industry/energy/power/new-power-story/articleshow/54321.cms</link>
<pubDate>Tue, 25 Aug 2026 05:30:00 +0530</pubDate></item>
<item><title>Out of scope</title><link>https://economictimes.indiatimes.com/markets/stocks/news/out/articleshow/999.cms</link></item>
<item><title>Duplicate</title><link>https://economictimes.indiatimes.com/industry/renewables/new-solar-story/articleshow/12345.cms</link></item>
</channel></rss>"""


def test_parse_feed_filters_scope_and_deduplicates() -> None:
    stories = parse_feed(RSS)
    assert len(stories) == 2
    assert [story.key for story in stories] == ["articleshow:54321", "articleshow:12345"]
    assert stories[1].title == "New solar story"
    assert stories[1].published_at is not None
    assert stories[1].published_at.tzinfo == timezone.utc
    assert stories[1].excerpt == "Key detail."


def test_parse_page_extracts_configured_article_sections() -> None:
    html = """
    <div class="listfullDiv"><ul>
      <li><a class="ancs" title="Solar title" href="/industry/renewables/solar/articleshow/12.cms">Solar title</a></li>
      <li><a title="Other" href="/markets/stocks/news/other/articleshow/13.cms">Other</a></li>
      <li><a href="/industry/renewables/wind/articleshow/14.cms">Wind title</a></li>
      <li><a href="/industry/energy/power/grid/articleshow/15.cms">Power title</a></li>
      <time class="date-format" data-time="Aug 25, 2026, 05:30 PM IST">Aug 25, 2026, 05:30 PM IST</time>
    </ul></div>
    """
    stories = parse_page(html, page_url="https://economictimes.indiatimes.com/industry/renewables/solar-energy")
    assert [story.key for story in stories] == ["articleshow:12", "articleshow:14", "articleshow:15"]
    assert stories[2].published_at == datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


class _FakeResponse:
    def __init__(self, content: bytes | str) -> None:
        self.content = content.encode() if isinstance(content, str) else content
        self.text = self.content.decode()

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = responses

    def get(self, url: str, *, timeout: int) -> _FakeResponse:
        return self.responses[url]


def test_fetch_stories_merges_rss_and_all_page_sources() -> None:
    solar_url = "https://economictimes.indiatimes.com/industry/renewables/solar-energy"
    power_url = "https://economictimes.indiatimes.com/industry/energy/power"
    renewables_url = "https://economictimes.indiatimes.com/industry/renewables"
    power_page = """
    <a href="/industry/energy/power/page-only/articleshow/99999.cms">Page-only power story</a>
    <time data-time="Aug 25, 2026, 06:30 PM IST">Aug 25, 2026, 06:30 PM IST</time>
    """
    renewables_page = '<a href="/industry/renewables/page-only/articleshow/88888.cms">Page-only renewables story</a>'
    session = _FakeSession(
        {
            "rss": _FakeResponse(RSS),
            solar_url: _FakeResponse('<a href="/industry/renewables/new-solar-story/articleshow/12345.cms">Solar title</a>'),
            power_url: _FakeResponse(power_page),
            renewables_url: _FakeResponse(renewables_page),
        }
    )
    scraper = EconomicTimesScraper(
        page_url=solar_url,
        rss_url="rss",
        session=session,
        max_stories=2,
        additional_page_urls=(power_url, renewables_url),
    )

    stories = scraper.fetch_stories()

    assert {story.key for story in stories} == {
        "articleshow:12345",
        "articleshow:54321",
        "articleshow:99999",
        "articleshow:88888",
    }


def test_parse_article_prefers_readable_article_body() -> None:
    html = """
    <html><head>
      <meta name="description" content="Short summary">
      <meta property="og:title" content="Meta title">
    </head><body>
      <div class="article_block" data-authors="ET Bureau" data-artdate="Aug 24, 2026, 05:30:00 AM IST">
        <h1 class="artTitle">A real title</h1>
        <p class="summary">A short summary.</p>
        <article><div class="artText"><p>First paragraph with the important facts.</p><p>Second paragraph with more details.</p></div></article>
      </div>
    </body></html>
    """
    article = parse_article(html)
    assert article.title == "A real title"
    assert article.summary == "A short summary."
    assert "First paragraph" in article.body
    assert article.author == "ET Bureau"


def test_duplicate_story_preserves_listing_sources_and_rss_excerpt() -> None:
    solar = 'https://economictimes.indiatimes.com/industry/renewables/solar-energy'
    renewables = 'https://economictimes.indiatimes.com/industry/renewables'
    listing = '<a href="/industry/renewables/new-solar-story/articleshow/12345.cms">Solar title</a>'
    scraper = EconomicTimesScraper(solar, 'rss', session=_FakeSession({'rss': _FakeResponse(RSS), solar: _FakeResponse(listing), renewables: _FakeResponse(listing)}), additional_page_urls=(renewables,))
    story = next(s for s in scraper.fetch_stories() if s.key == 'articleshow:12345')
    assert story.source_pages == (solar, renewables)
    assert story.excerpt == 'Key detail.'


@pytest.mark.parametrize("metadata", [
    '<meta property="article:published_time" content="2026-11-12T04:25:00+05:30">',
    '<meta itemprop="datePublished" content="2026-11-11T22:55:00Z">',
    '<div class="article_block" data-artidate="Nov 12, 2026, 04:25:00 AM IST" data-artdate="Nov 13, 2026, 04:25:00 AM IST"></div>',
    '<script type="application/ld+json">{"@type":"NewsArticle","datePublished":"2026-11-12T04:25:00+05:30","dateModified":"2026-11-13T04:25:00+05:30"}</script>',
])
def test_article_reads_original_publication_timestamp(metadata):
    article = parse_article('<h1 class="artTitle">Title</h1><div class="artText">Body</div>' + metadata)
    assert getattr(article, "published_at", None) == datetime(2026, 11, 11, 22, 55, tzinfo=timezone.utc)


@pytest.mark.parametrize("metadata", [
    '<script type="application/ld+json">invalid json</script>',
    '<script type="application/ld+json">{"@type":"NewsArticle","dateModified":"2026-11-11T22:55:00Z"}</script>',
    '<script type="application/ld+json">{"@type":"Organization","datePublished":"2026-11-11T22:55:00Z"}</script>',
    '<div class="article_block" data-artdate="Nov 12, 2026, 04:25:00 AM IST"></div><script type="application/ld+json">{"@type":"NewsArticle","dateModified":"2026-11-11T22:55:00Z"}</script>',
])
def test_article_does_not_invent_publication_timestamp(metadata):
    article = parse_article('<h1 class="artTitle">Title</h1><div class="artText">Body</div>' + metadata)
    assert getattr(article, "published_at", None) is None


def test_duplicate_page_anchor_keeps_later_publication_time():
    html = '''
      <a href="/industry/renewables/a/articleshow/1.cms">Thumbnail</a>
      <a href="/industry/renewables/a/articleshow/1.cms">Headline</a>
      <time datetime="2026-11-11T22:55:00Z"></time>
    '''
    stories = parse_page(html, page_url="https://economictimes.indiatimes.com/industry/renewables")
    assert len(stories) == 1
    assert stories[0].published_at == datetime(2026, 11, 11, 22, 55, tzinfo=timezone.utc)
