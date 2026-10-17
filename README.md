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

## SMS reminders

`uv run manage.py notify_upcoming_races` texts tippers about races in the next day. Options: `--days N`, `--dryrun`, `--resend`.

## FIS scraping

- FIS blocks scrapers that don't look like a browser, so all requests go through `fis_get` in `fis_connector.py` with browser headers.
- FIS returns 429 after roughly 40 requests in a short window. `fis_get` raises `FISRateLimited`, and the views show a warning instead of an error. Wait a minute and retry.
- Page layouts change most seasons. The first import of the season is the usual place for breakage: check the calendar import, start list update and one race result.
