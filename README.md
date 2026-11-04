# Economic Times energy, infrastructure, and policy alerts

This service checks the Economic Times Solar Energy, Energy/Power, Renewables, Infrastructure, and Policy pages, finds stories, extracts the public article text, asks OpenAI's Responses API for a concise summary, and sends the summary plus the article link to Gmail.

## What was inspected

The target pages are server-rendered. The service uses this RSS feed plus all five page listings:

`https://economictimes.indiatimes.com/rssfeeds/cfmid-4005094.cms`

- Solar Energy: `https://economictimes.indiatimes.com/industry/renewables/solar-energy`
- Energy / Power: `https://economictimes.indiatimes.com/industry/energy/power`
- Renewables: `https://economictimes.indiatimes.com/industry/renewables`
- Infrastructure: `https://economictimes.indiatimes.com/news/economy/infrastructure`
- Policy: `https://economictimes.indiatimes.com/news/economy/policy`

The implementation uses the feed as supplemental discovery because it contains titles, links, excerpts, publication times, and stable article IDs. It also fetches all five page listings on every poll and merges the results, so a non-empty but incomplete feed cannot hide an article listed on any monitored page. Article pages expose the summary in metadata and readable article text in `.artText`.

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
   OPENAI_MODEL=gpt-6-luna
   OPENAI_REASONING_EFFORT=medium
   ```

   ChatGPT Plus and the API platform use separate billing systems. An API key and API billing setup are required for the background summarizer; the Plus subscription itself is not an API credential. See the [official billing guidance](https://help.openai.com/en/articles/8156019-is-api-usage-included-in-chatgpt-subscriptions-even-if-i-have-a-paid-chatgpt-account) and the [GPT-6 Luna model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna).

## Run it

First, verify discovery without sending email or changing state:

```bash
python -m solar_alerts --dry-run
```

The first real run creates `state.json` and baselines the current listings without sending the existing stories. This prevents a first-run inbox flood. To intentionally alert on the current stories, use `--notify-current`. When a new listing is added to an already initialized deployment, its currently listed stories are eligible for alerts, subject to the per-run limit and deduplication.

For a single poll, suitable for cron:

```bash
python -m solar_alerts --once
```

For a persistent process:

```bash
python -m solar_alerts --watch --interval 900
```

## GitHub Actions deployment

The repository includes `.github/workflows/renewables-alerts.yml`, which requests a poll every 5 minutes and can also be started manually from the Actions tab. It stores `state.json` in the repository after each run so duplicate emails are suppressed across ephemeral GitHub-hosted runners.

Add these repository secrets before enabling the workflow:

```bash
gh secret set EMAIL_SENDER --repo OWNER/REPO
gh secret set EMAIL_PASSWORD --repo OWNER/REPO
gh secret set EMAIL_RECEIVER --repo OWNER/REPO
gh secret set OPENAI_API_KEY --repo OWNER/REPO
```

Each command reads the value interactively. The Gmail and OpenAI secrets are never committed to the repository. Scheduled workflows can be delayed or skipped by GitHub. On Amir’s Mac, the `com.amir.economic-times-alerts-trigger` launch agent additionally dispatches the same GitHub workflow every five minutes while the Mac is awake and online. GitHub workflow concurrency serializes the senders and their shared state. This fallback cannot run while the Mac is asleep or offline, and neither trigger guarantees immediate delivery. Its log is `~/Library/Logs/economic-times-alerts-trigger.log`.

Email subjects and headers identify the monitored listing pages where a story appeared; RSS-only stories use their article section. A story listed on multiple pages shows those page names. If no readable article text or excerpt is available, the alert says so and links to the article without an AI summary. A fixed `EMAIL_SUBJECT_PREFIX` overrides the automatic subject prefix.

The program prioritizes the newest dated unseen stories ahead of older and undated backlog items. It sends one email per new story and marks an article as seen only after summarization and email delivery succeed. Failed items remain eligible for retry on the next poll.

Example cron entry (run from this repository):

```cron
*/15 * * * * cd /absolute/path/to/economic-times-alerts && .venv/bin/python -m solar_alerts --once >> alerts.log 2>&1
```

## Configuration

See `.env.example` for all optional settings. Important defaults are:

- `SOURCE_PAGE_URL`, `SOURCE_POWER_PAGE_URL`, `SOURCE_RENEWABLES_PAGE_URL`, `SOURCE_INFRASTRUCTURE_PAGE_URL`, and `SOURCE_POLICY_PAGE_URL` select the five monitored pages.
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
