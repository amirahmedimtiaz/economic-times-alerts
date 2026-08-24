from types import SimpleNamespace

from solar_alerts.models import ArticleContent, Story
from solar_alerts.summarizer import OpenAISummarizer


class FakeResponses:
    def __init__(self) -> None:
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text="- What happened\n- Key detail\n- Why it matters")


class FakeClient:
    def __init__(self, responses) -> None:
        self.responses = responses


def test_summarizer_uses_luna_and_max_reasoning() -> None:
    responses = FakeResponses()
    summarizer = OpenAISummarizer(
        api_key="test",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        client=FakeClient(responses),
    )
    story = Story(title="Solar story", url="https://economictimes.indiatimes.com/industry/renewables/x/articleshow/1.cms")
    article = ArticleContent(title="Solar story", summary="Summary", body="Full article text")
    assert summarizer.summarize(story, article).startswith("- What happened")
    assert responses.calls[0]["model"] == "gpt-5.6-luna"
    assert responses.calls[0]["reasoning"] == {"effort": "max"}
    assert responses.calls[0]["store"] is False
