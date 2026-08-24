# Economic Times solar/renewables alerts

This service checks the Economic Times renewables page, finds newly published stories, extracts the public article text, asks OpenAI's Responses API for a concise summary, and sends the summary plus the article link to Gmail.

## What was inspected

The target page is server-rendered and currently advertises this RSS feed in its HTML:

`https://economictimes.indiatimes.com/rssfeeds/cfmid-4005094.cms`

The implementation uses that feed first because it contains titles, links, excerpts, publication times, and stable article IDs. If the feed is unavailable, it falls back to article links in the page's `.listfullDiv`; the page also exposes numbered pagination for older history. Article pages expose the summary in metadata and readable article text in `.artText`.

The scraper stays within the public Economic Times pages and does not attempt to bypass a paywall. Keep the polling interval reasonable and review the site's terms before running it continuously.

## Setup

1. Create an environment and install dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. The repository already has a `.env` with `EMAIL_SENDER`, `EMAIL_PASSWORD`, and `EMAIL_RECEIVER`. Keep that file private. `EMAIL_PASSWORD` should be a Gmail app password when two-step verification is enabled.

3. Add an OpenAI API key to `.env`:

   ```dotenv
   OPENAI_API_KEY=sk-...
   OPENAI_MODEL=gpt-5.6-luna
   OPENAI_REASONING_EFFORT=max
   ```

   ChatGPT Plus and the API platform use separate billing systems. An API key and API billing setup are required for the background summarizer; the Plus subscription itself is not an API credential. See the [official billing guidance](https://help.openai.com/en/articles/8156019-is-api-usage-included-in-chatgpt-subscriptions-even-if-i-have-a-paid-chatgpt-account) and the [GPT-5.6 Luna model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

## Run it

First, verify discovery without sending email or changing state:

```bash
python -m solar_alerts --dry-run
```

The first real run creates `state.json` and baselines the current feed without sending the existing stories. This prevents a first-run inbox flood. To intentionally alert on the current stories, use `--notify-current`.

For a single poll, suitable for cron:

```bash
python -m solar_alerts --once
```

For a persistent process:

```bash
python -m solar_alerts --watch --interval 900
```

## GitHub Actions deployment

The repository includes `.github/workflows/renewables-alerts.yml`, which polls every 15 minutes and can also be started manually from the Actions tab. It stores `state.json` in the private repository after each run so duplicate emails are suppressed across ephemeral GitHub-hosted runners.

Add these repository secrets before enabling the workflow:

```bash
gh secret set EMAIL_SENDER --repo OWNER/REPO
gh secret set EMAIL_PASSWORD --repo OWNER/REPO
gh secret set EMAIL_RECEIVER --repo OWNER/REPO
gh secret set OPENAI_API_KEY --repo OWNER/REPO
```

Each command reads the value interactively. The Gmail and OpenAI secrets are never committed to the repository. Scheduled workflows can be delayed by GitHub during periods of high demand; use `--watch` locally if a tighter cadence is important.

The program sends one email per new story and marks an article as seen only after summarization and email delivery succeed. Failed items remain eligible for retry on the next poll.

Example cron entry (run from this repository):

```cron
*/15 * * * * cd /absolute/path/to/economic-times-alerts && .venv/bin/python -m solar_alerts --once >> alerts.log 2>&1
```

## Configuration

See `.env.example` for all optional settings. Important defaults are:

- `POLL_INTERVAL_SECONDS=900`
- `MAX_ARTICLES_PER_RUN=10`
- `ARTICLE_MAX_CHARS=18000`
- `STATE_FILE=state.json`
- `EMAIL_SMTP_HOST=smtp.gmail.com`
- `EMAIL_SMTP_PORT=465`

`EMAIL_RECEIVER` accepts one address or a comma-separated list.

## Tests

```bash
pytest -q
```
