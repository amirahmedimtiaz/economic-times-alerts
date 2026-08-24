from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from html import escape
import re
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
    summary_html = _render_summary_html(summary)
    title = escape(story.title)
    url = escape(story.canonical_url, quote=True)
    published = escape(_published_label(story.published_at))
    return f"""<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background:#f4f6f8;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;-webkit-font-smoothing:antialiased">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent">{title}</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="width:100%;background:#f4f6f8">
      <tr>
        <td align="center" style="padding:28px 16px">
          <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:600px;background:#ffffff;border:1px solid #e5e9ef;border-radius:14px;overflow:hidden">
            <tr>
              <td style="padding:22px 28px 20px;background:#13223a">
                <p style="margin:0 0 7px;color:#9fdbff;font-size:12px;font-weight:700;letter-spacing:0.09em;text-transform:uppercase">Economic Times · Renewables</p>
                <p style="margin:0;color:#ffffff;font-size:18px;font-weight:700;line-height:1.3">New story alert</p>
              </td>
            </tr>
            <tr>
              <td style="padding:28px">
                <h1 style="margin:0 0 12px;color:#172033;font-size:24px;font-weight:700;line-height:1.28;letter-spacing:-0.01em">{title}</h1>
                <p style="margin:0 0 26px;color:#667085;font-size:14px;line-height:1.5">Published {published}</p>

                <div style="margin:0 0 24px;padding:18px 20px;background:#f7faff;border-left:4px solid #1f7ae0;border-radius:0 8px 8px 0">
                  <p style="margin:0 0 11px;color:#315d8f;font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase">AI summary</p>
                  {summary_html}
                </div>

                <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                  <tr>
                    <td align="center" bgcolor="#1769c2" style="border-radius:8px">
                      <a href="{url}" style="display:inline-block;padding:13px 18px;color:#ffffff;font-size:16px;font-weight:700;line-height:1;text-decoration:none">Read full article&nbsp; →</a>
                    </td>
                  </tr>
                </table>

                <p style="margin:26px 0 0;color:#8a94a6;font-size:12px;line-height:1.5">Automated alert with an AI-generated summary. The source article remains the authority.</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def _render_summary_html(summary: str) -> str:
    lines = [line.strip() for line in summary.strip().splitlines() if line.strip()]
    items = [re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)", "", line) for line in lines]
    if len(items) > 1:
        rendered_items = "".join(
            f'<li style="margin:0 0 9px;color:#27364d;font-size:16px;line-height:1.55">{_format_summary_inline(item)}</li>'
            for item in items
        )
        return f'<ul style="margin:0;padding:0 0 0 20px">{rendered_items}</ul>'
    text = _format_summary_inline(items[0] if items else "Summary unavailable.")
    return f'<p style="margin:0;color:#27364d;font-size:16px;line-height:1.6">{text}</p>'


def _format_summary_inline(text: str) -> str:
    """Render the small Markdown subset the summarizer is asked to use safely."""

    escaped = escape(text)
    rendered = re.sub(
        r"\*\*(.+?)\*\*",
        r'<strong style="font-weight:700;color:#172033">\1</strong>',
        escaped,
    )
    return rendered.replace("**", "")


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
