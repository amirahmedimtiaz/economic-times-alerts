from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import re
from urllib.parse import urlsplit, urlunsplit


ARTICLE_ID_RE = re.compile(r"/articleshow/(\d+)(?:\.cms)?(?:/)?$", re.IGNORECASE)


def canonicalize_url(url: str) -> str:
    """Return a stable Economic Times URL without tracking parameters."""

    parsed = urlsplit(url.strip())
    host = parsed.netloc.lower()
    if host == "m.economictimes.indiatimes.com":
        host = "economictimes.indiatimes.com"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunsplit(("https", host, path, "", ""))


@dataclass(frozen=True, slots=True)
class Story:
    title: str
    url: str
    excerpt: str = ""
    published_at: datetime | None = None
    source: str = "rss"

    @property
    def canonical_url(self) -> str:
        return canonicalize_url(self.url)

    @property
    def key(self) -> str:
        match = ARTICLE_ID_RE.search(urlsplit(self.canonical_url).path)
        if match:
            return f"articleshow:{match.group(1)}"
        digest = sha256(self.canonical_url.encode("utf-8")).hexdigest()[:24]
        return f"url:{digest}"


@dataclass(frozen=True, slots=True)
class ArticleContent:
    title: str
    summary: str
    body: str
    author: str = ""
    published_at_text: str = ""
