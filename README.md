# ski-tipp

Ski-Tipp runs a tipping competition on the men's Alpine Ski World Cup. Tippers pick the podium (and a DNF for tech events) before each race, and the leaderboard scores them once FIS publishes results. Races, start lists and results are scraped from fis-ski.com.

Official software of the International Ski Tipping Verein. Built on Django, running at https://www.ski-tipp.org.

Staff instructions for running a season are on the site at `/app/guide/` (user menu → Staff guide).

## Local setup

Needs [uv](https://docs.astral.sh/uv/). It installs the right Python version itself.

```
uv sync
DJANGO_SECRET_KEY=dev DJANGO_DEBUG=True uv run manage.py migrate
DJANGO_SECRET_KEY=dev DJANGO_DEBUG=True uv run manage.py createsuperuser
DJANGO_SECRET_KEY=dev DJANGO_DEBUG=True uv run manage.py runserver
```

To work with real data, copy a season database (e.g. `ski-tipp-db_2026.sqlite3`) and point `DB_LOCATION` at the copy.

Environment variables:

| Variable | Purpose |
| --- | --- |
| `DJANGO_SECRET_KEY` | required |
| `DJANGO_DEBUG` | `True` for local development only |
| `DB_LOCATION` | SQLite file, defaults to `ski-tipp-db.sqlite3` in the repo |
| `TIPP_ASSIST_DATA` | folder for Tipp-Assist's data (see `tipbot/README.md`), defaults to `tipbot/data` in the repo |
| `ALLOWED_HOSTS` | JSON list, defaults to ski-tipp.org and localhost |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_FROM_EMAIL` | email via SES and SMS reminders via SNS. Without them, emails are written to `sent_emails/` |

On the server these live in `/opt/bitnami/projects/ski-tipp/.env`, which the gunicorn service loads. It is not in git.

## Start of a new season

1. Deploy the latest code (below).
2. In `skitipp/fis_connector.py`, check `update_ws_start_list` uses the new season code. FIS numbers seasons by their end year, so 2026/27 is `seasoncode=2027`.
3. Follow "Start a new season" in the staff guide on the site.

## Dependencies

`pyproject.toml` lists them, `uv.lock` pins exact versions. jupyter and ipython are dev-only.

```
uv lock --upgrade-package django
uv export --no-dev --no-hashes --no-emit-project -o requirements.txt
```

Commit `uv.lock` and `requirements.txt` together.

## Deployment

Production is a Bitnami server. Gunicorn runs as the systemd service `gunicorn` from `.venv/bin/gunicorn`.

```
ssh bitnami@<server>
cd /opt/bitnami/projects/ski-tipp && deploy/deploy.sh gunicorn
```

`deploy.sh` pulls, runs `uv sync`, collects static files and restarts the service. It does not run migrations. When a change adds one, load the server's environment first so it hits the live database:

```
set -a; . ./.env; set +a
uv run manage.py migrate
```

## Reminders

`uv run manage.py notify_upcoming_races` reminds tippers about races in the next 24 hours, but only when they have no tipp yet or a podium pick who isn't on the start list. Everyone gets an email, and members of the Tipp-Assist group with a mobile number (Admin → Users) also get an SMS. Each tipper gets at most one reminder per race. Options: `--hours N`, `--better-by P`, `--dryrun`, `--resend`.

The SMS also carries Tipp-Assist's tipp. With no tipp yet, it suggests the best one. With a tipp, the SMS goes out when Tipp-Assist's best tipp beats it by at least 0.25 expected points (`--better-by`), even if nothing else is wrong with it.

To check that SMS works, run `uv run manage.py send_test_sms +41791234567` on the server with `.env` loaded. It shows whether the AWS account is in the SMS sandbox and its spend limit, then sends a test message. Add `--race <FIS id> --user <username>` to send that race's reminder SMS with Tipp-Assist's tipp, and `--dryrun` to only print it. Reminder SMS are kept to one 160 character message: accents are dropped, and long surnames are cut to their last word.

The server runs it once a day at 8:00 Zurich time from a systemd timer, so it covers that day's races, which start at 9:00 at the earliest. To install the timer:

```
sudo cp deploy/ski-tipp-reminders.service deploy/ski-tipp-reminders.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ski-tipp-reminders.timer
```

When FIS hasn't published a start time yet, races are imported at 9:00 local time, the earliest they start, and get their real time when the race is updated.

## FIS scraping

- FIS blocks scrapers that don't look like a browser, so all requests go through `fis_get` in `fis_connector.py` with browser headers.
- FIS returns 429 after roughly 40 requests in a short window. `fis_get` raises `FISRateLimited`, and the views show a warning instead of an error. Wait a minute and retry.
- Page layouts change most seasons. The first import of the season is the usual place for breakage: check the calendar import, start list update and one race result.
