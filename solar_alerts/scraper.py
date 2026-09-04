from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
import logging
import re
from typing import Iterable
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

import requests

from .models import ArticleContent, Story, canonicalize_url


LOGGER = logging.getLogger(__name__)
ARTICLE_PATH_RE = re.compile(
    r"^/industry/(?:renewables|energy/power)/.*/articleshow/\d+\.cms/?$",
    re.IGNORECASE,
)
ARTICLE_LINK_RE = re.compile(r"/articleshow/\d+\.cms(?:/)?$", re.IGNORECASE)
SKIP_TAGS = {"script", "style", "noscript", "iframe", "svg"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class ScraperError(RuntimeError):
    """Raised when a source cannot be downloaded or parsed."""


def clean_text(value: str) -> str:
    value = unescape(unescape(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _clean_body(value: str) -> str:
    value = unescape(unescape(value or ""))
    value = re.sub(r"[ \t\f\r]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def parse_feed(content: bytes, *, limit: int = 50) -> list[Story]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ScraperError("Economic Times RSS returned invalid XML") from exc

    stories: list[Story] = []
    seen: set[str] = set()
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1].lower() != "item":
            continue
        fields = {child.tag.rsplit("}", 1)[-1].lower(): (child.text or "") for child in item}
        url = canonicalize_url(fields.get("link", ""))
        path = urlsplit(url).path
        if not ARTICLE_PATH_RE.match(path) or url in seen:
            continue
        seen.add(url)
        published_at = _parse_feed_date(fields.get("pubdate", ""))
        stories.append(
            Story(
                title=clean_text(fields.get("title", "")),
                url=url,
                excerpt=clean_text(fields.get("description", "")),
                published_at=published_at,
                source="rss",
            )
        )

    stories.sort(
        key=lambda story: story.published_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return stories[:limit]


def _parse_feed_date(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class _PageLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._anchor: dict[str, str] | None = None
        self._anchor_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a" or self._anchor is not None:
            return
        self._anchor = {key.lower(): value or "" for key, value in attrs}
        self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._anchor is None:
            return
        href = unescape(self._anchor.get("href", "")).strip()
        title = clean_text(self._anchor.get("title", ""))
        text = clean_text(" ".join(self._anchor_text))
        if not title or title.lower() == "title":
            title = text
        self.links.append((href, title))
        self._anchor = None
        self._anchor_text = []


def parse_page(content: str, *, page_url: str, limit: int = 50) -> list[Story]:
    parser = _PageLinkParser()
    parser.feed(content)
    parser.close()

    stories: list[Story] = []
    seen: set[str] = set()
    for href, title in parser.links:
        url = canonicalize_url(urljoin(page_url, href))
        if not ARTICLE_LINK_RE.search(urlsplit(url).path):
            continue
        if not ARTICLE_PATH_RE.match(urlsplit(url).path) or url in seen:
            continue
        seen.add(url)
        stories.append(Story(title=title or url.rsplit("/", 1)[-1], url=url, source="page"))
        if len(stories) >= limit:
            break
    return stories


class _ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.summary_parts: list[str] = []
        self.body_blocks: list[list[str]] = []
        self.author = ""
        self.published_at_text = ""
        self._ignored_depth = 0
        self._body_depth = 0
        self._title_active = False
        self._summary_depth = 0
        self._author_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr = {key.lower(): value or "" for key, value in attrs}
        if self._ignored_depth:
            if tag not in VOID_TAGS:
                self._ignored_depth += 1
            return
        if tag in SKIP_TAGS:
            self._ignored_depth = 1
            return
        if tag == "meta":
            key = (attr.get("name") or attr.get("property") or "").lower()
            if key and attr.get("content"):
                self.meta[key] = attr["content"]
        if tag == "div" and "article_block" in attr.get("class", "").split():
            self.author = clean_text(attr.get("data-authors", ""))
            self.published_at_text = clean_text(attr.get("data-artdate", ""))

        classes = set(attr.get("class", "").split())
        if tag == "h1" and "artTitle" in classes:
            self._title_active = True
        if tag == "p" and "summary" in classes:
            self._summary_depth = 1
        elif self._summary_depth and tag not in VOID_TAGS:
            self._summary_depth += 1
        if tag == "div" and "artText" in classes:
            self.body_blocks.append([])
            self._body_depth = 1
        elif self._body_depth and tag not in VOID_TAGS:
            self._body_depth += 1

        if self._body_depth and tag in {"p", "div", "li", "br", "h2", "h3"}:
            self.body_blocks[-1].append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._body_depth:
            if tag in {"p", "div", "li", "br", "h2", "h3"}:
                self.body_blocks[-1].append("\n")
            if tag not in VOID_TAGS:
                self._body_depth -= 1
        if self._summary_depth:
            if tag not in VOID_TAGS:
                self._summary_depth -= 1
        if tag == "h1":
            self._title_active = False

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._title_active:
            self.title_parts.append(data)
        if self._summary_depth:
            self.summary_parts.append(data)
        if self._body_depth:
            self.body_blocks[-1].append(data)


def parse_article(content: str) -> ArticleContent:
    parser = _ArticleParser()
    parser.feed(content)
    parser.close()

    title = clean_text(" ".join(parser.title_parts))
    if not title:
        title = clean_text(parser.meta.get("og:title") or parser.meta.get("twitter:title", ""))
    if not title:
        title = clean_text(parser.meta.get("title", ""))

    summary = clean_text(" ".join(parser.summary_parts))
    if not summary:
        summary = clean_text(parser.meta.get("description") or parser.meta.get("og:description", ""))

    bodies = [_clean_body("".join(block)) for block in parser.body_blocks]
    body = max(bodies, key=len, default="")
    if not body:
        body = summary
    if not title and not body:
        raise ScraperError("Economic Times article did not contain readable content")
    return ArticleContent(
        title=title,
        summary=summary,
        body=body,
        author=parser.author,
        published_at_text=parser.published_at_text,
    )


@dataclass(slots=True)
class EconomicTimesScraper:
    page_url: str
    rss_url: str
    timeout_seconds: int = 30
    max_stories: int = 50
    session: requests.Session | None = None
    additional_page_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "economic-times-alerts/0.1 (+https://github.com/)",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            }
        )

    def _get(self, url: str) -> requests.Response:
        assert self.session is not None
        try:
            response = self.session.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScraperError(f"Could not fetch {url}: {exc}") from exc
        return response

    def _page_urls(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.page_url, *self.additional_page_urls)))

    def fetch_stories(self) -> list[Story]:
        rss_stories: list[Story] = []
        try:
            response = self._get(self.rss_url)
            rss_stories = parse_feed(response.content, limit=self.max_stories)
            if not rss_stories:
                LOGGER.warning("RSS feed returned no in-scope stories; trying page HTML")
        except ScraperError as exc:
            LOGGER.warning("RSS discovery failed: %s; relying on page HTML", exc)

        page_stories: list[Story] = []
        for page_url in self._page_urls():
            try:
                response = self._get(page_url)
            except ScraperError as exc:
                LOGGER.warning("Page discovery failed for %s: %s", page_url, exc)
                continue
            page_stories.extend(parse_page(response.text, page_url=page_url, limit=self.max_stories))

        stories_by_key: dict[str, Story] = {}
        for story in (*rss_stories, *page_stories):
            existing = stories_by_key.get(story.key)
            if existing is None or (existing.published_at is None and story.published_at is not None):
                stories_by_key[story.key] = story

        stories = list(stories_by_key.values())
        stories.sort(
            key=lambda story: story.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        stories = stories[: self.max_stories]
        if not stories:
            raise ScraperError("Economic Times sources did not contain any in-scope article links")
        return stories

    def fetch_article(self, story: Story) -> ArticleContent:
        response = self._get(story.canonical_url)
        article = parse_article(response.text)
        if len(article.body) > 0:
            return article
        if story.excerpt:
            return ArticleContent(title=story.title, summary=story.excerpt, body=story.excerpt)
        raise ScraperError(f"Could not extract readable content from {story.url}")
