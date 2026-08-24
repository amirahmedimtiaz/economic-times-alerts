from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .models import ArticleContent, Story


class SummarizationError(RuntimeError):
    """Raised when OpenAI cannot produce an article summary."""


@dataclass(slots=True)
class OpenAISummarizer:
    api_key: str
    model: str = "gpt-5.6-luna"
    reasoning_effort: str = "max"
    max_input_chars: int = 18_000
    max_output_tokens: int = 700
    client: object | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - dependency installation issue
                raise SummarizationError("Install requirements.txt to use OpenAI summarization") from exc
            self.client = OpenAI(api_key=self.api_key)

    def summarize(self, story: Story, article: ArticleContent) -> str:
        body = (article.body or article.summary or story.excerpt).strip()
        body = body[: self.max_input_chars]
        if not body:
            raise SummarizationError(f"Article {story.key} has no text to summarize")

        prompt = f"""Summarize the following Economic Times article for a busy reader.

Requirements:
- Be factual and use only information present in the supplied article.
- Return exactly three concise plain-text lines in this format:
  What happened: <summary>
  Key details: <key numbers, entities, or milestones>
  Why it matters: <business or industry relevance>
- Do not use Markdown, bullets, asterisks, or bold markers.
- Do not mention that you are an AI and do not invent a headline, facts, sources, or advice.
- Keep the result below 160 words.

Headline: {story.title}
Published: {story.published_at.isoformat() if story.published_at else "unknown"}

Article text:
{body}
"""
        try:
            assert self.client is not None
            response = self.client.responses.create(
                model=self.model,
                instructions="You are a concise, careful business-news editor.",
                input=prompt,
                reasoning={"effort": self.reasoning_effort},
                max_output_tokens=self.max_output_tokens,
                store=False,
                safety_identifier=hashlib.sha256(story.key.encode("utf-8")).hexdigest()[:32],
            )
        except Exception as exc:
            raise SummarizationError(f"OpenAI summarization failed for {story.key}: {exc}") from exc

        result = str(getattr(response, "output_text", "") or "").strip()
        if not result:
            raise SummarizationError(f"OpenAI returned an empty summary for {story.key}")
        return result
