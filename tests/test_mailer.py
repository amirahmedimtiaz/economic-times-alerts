from solar_alerts.mailer import render_html_alert, render_text_alert
from solar_alerts.models import Story


def test_alert_renderers_escape_and_include_link() -> None:
    story = Story(title="A <new> story", url="https://economictimes.indiatimes.com/industry/renewables/x/articleshow/1.cms")
    text = render_text_alert(story, "- A factual summary\n- A second factual detail")
    html = render_html_alert(story, "- A factual summary\n- A second factual detail")
    assert "A <new> story" in text
    assert story.canonical_url in text
    assert "A &lt;new&gt; story" in html
    assert story.canonical_url in html
    assert "New story alert" in html
    assert "font-size:16px" in html
    assert "<ul" in html
    assert "Read full article" in html
