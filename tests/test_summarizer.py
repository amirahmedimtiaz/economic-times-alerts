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
        model="gpt-6-luna",
        reasoning_effort="max",
        client=FakeClient(responses),
    )
    story = Story(title="Solar story", url="https://economictimes.indiatimes.com/industry/renewables/x/articleshow/1.cms")
    article = ArticleContent(title="Solar story", summary="Summary", body="Full article text")
    assert summarizer.summarize(story, article).startswith("- What happened")
    assert responses.calls[0]["model"] == "gpt-6-luna"
    assert responses.calls[0]["reasoning"] == {"effort": "max"}
    assert responses.calls[0]["store"] is False


def test_summary_retries_when_reasoning_exhausts_output_budget() -> None:
    class ExhaustedResponses:
        def __init__(self):
            self.calls = []
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return SimpleNamespace(output_text='', status='incomplete', incomplete_details=SimpleNamespace(reason='max_output_tokens'))
            return SimpleNamespace(output_text='A useful summary', status='completed', incomplete_details=None)

    responses = ExhaustedResponses()
    summarizer = OpenAISummarizer(api_key='test', client=FakeClient(responses))
    story = Story(title='News', url='https://economictimes.indiatimes.com/industry/renewables/x/articleshow/1.cms')
    article = ArticleContent(title='News', summary='', body='Article text')
    assert summarizer.summarize(story, article) == 'A useful summary'
    assert responses.calls[1]['max_output_tokens'] > responses.calls[0]['max_output_tokens']
