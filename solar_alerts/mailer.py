from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from html import escape
import smtplib

from .models import Story


def _published_label(published_at: datetime | None) -> str:
    if published_at is None:
        return "Publication time unavailable"
    return published_at.astimezone().strftime("%d %b %Y, %H:%M %Z")


def render_text_alert(story: Story, summary: str) -> str:
    return "\n".join(
        [
            story.title,
            "",
            f"Published: {_published_label(story.published_at)}",
            f"Source: {story.canonical_url}",
            "",
            "AI summary:",
            summary.strip(),
            "",
            f"Read the article: {story.canonical_url}",
        ]
    )


def render_html_alert(story: Story, summary: str) -> str:
    summary_html = escape(summary.strip()).replace("\n", "<br>\n")
    title = escape(story.title)
    url = escape(story.canonical_url, quote=True)
    published = escape(_published_label(story.published_at))
    return f"""<!doctype html>
<html><body style="font-family:Arial,sans-serif;line-height:1.5;color:#222">
  <h2 style="margin-bottom:8px">{title}</h2>
  <p style="color:#666;margin-top:0">Published: {published}</p>
  <h3>AI summary</h3>
  <p>{summary_html}</p>
  <p><a href="{url}">Read the full article on The Economic Times</a></p>
</body></html>"""


@dataclass(slots=True)
class GmailMailer:
    sender: str
    password: str
    receivers: tuple[str, ...]
    subject_prefix: str = "[ET Solar]"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 465
    smtp_ssl: bool = True
    timeout_seconds: int = 30

    def send_alert(self, story: Story, summary: str) -> None:
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = ", ".join(self.receivers)
        message["Subject"] = f"{self.subject_prefix} {story.title}".strip()
        message.set_content(render_text_alert(story, summary))
        message.add_alternative(render_html_alert(story, summary), subtype="html")

        if self.smtp_ssl:
            with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self.timeout_seconds) as smtp:
                smtp.login(self.sender, self.password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout_seconds) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(self.sender, self.password)
                smtp.send_message(message)
