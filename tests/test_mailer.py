from solar_alerts.mailer import render_html_alert, render_text_alert
from solar_alerts.models import Story


def test_alert_renderers_escape_and_include_link() -> None:
    story = Story(title="A <new> story", url="https://economictimes.indiatimes.com/industry/renewables/x/articleshow/1.cms")
    summary = "- **What happened:** A factual summary\n- **Key details:** A second factual detail\n- **Why it matters:** It affects the industry."
    text = render_text_alert(story, summary)
    html = render_html_alert(story, summary)
    assert "A <new> story" in text
    assert story.canonical_url in text
    assert "A &lt;new&gt; story" in html
    assert story.canonical_url in html
    assert "New story alert" in html
    assert "font-size:16px" in html
    assert "Read full article" in html
    assert "What happened</p>" in html
    assert "Key details</p>" in html
    assert "Why it matters</p>" in html
    assert "**What happened:**" not in html
