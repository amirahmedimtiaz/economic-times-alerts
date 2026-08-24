from datetime import timezone

from solar_alerts.scraper import parse_article, parse_feed, parse_page


RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title><![CDATA[New solar story]]></title>
<link>https://economictimes.indiatimes.com/industry/renewables/new-solar-story/articleshow/12345.cms?utm_source=x</link>
<description><![CDATA[<p>Key detail.</p>]]></description>
<pubDate>Mon, 24 Aug 2026 05:30:00 +0530</pubDate></item>
<item><title>Out of scope</title><link>https://economictimes.indiatimes.com/markets/stocks/news/out/articleshow/999.cms</link></item>
<item><title>Duplicate</title><link>https://economictimes.indiatimes.com/industry/renewables/new-solar-story/articleshow/12345.cms</link></item>
</channel></rss>"""


def test_parse_feed_filters_scope_and_deduplicates() -> None:
    stories = parse_feed(RSS)
    assert len(stories) == 1
    assert stories[0].title == "New solar story"
    assert stories[0].key == "articleshow:12345"
    assert stories[0].published_at is not None
    assert stories[0].published_at.tzinfo == timezone.utc
    assert stories[0].excerpt == "Key detail."


def test_parse_page_extracts_only_renewables_article_links() -> None:
    html = """
    <div class="listfullDiv"><ul>
      <li><a class="ancs" title="Solar title" href="/industry/renewables/solar/articleshow/12.cms">Solar title</a></li>
      <li><a title="Other" href="/markets/stocks/news/other/articleshow/13.cms">Other</a></li>
      <li><a href="/industry/renewables/wind/articleshow/14.cms">Wind title</a></li>
    </ul></div>
    """
    stories = parse_page(html, page_url="https://economictimes.indiatimes.com/industry/renewables/solar-energy")
    assert [story.key for story in stories] == ["articleshow:12", "articleshow:14"]


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
