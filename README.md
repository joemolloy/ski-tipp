# ski-tipp

Ski-Tipp is a plaform for running a Ski World Cup Tipping Comp, Built on Django.

It uses a webscraper to pull the races and results for various FIS competitions.

Official software of the International Ski Tipping Verein

## Development

Dependencies are managed with [uv](https://docs.astral.sh/uv/). `pyproject.toml` lists them and `uv.lock` pins exact versions.

```
uv sync                       # create/update .venv, including dev tools (jupyter, ipython)
uv run manage.py runserver
```

To upgrade a package: `uv lock --upgrade-package django`, then regenerate `requirements.txt`:

```
uv export --no-dev --no-hashes --no-emit-project -o requirements.txt
```

## Deployment

To deploy, SSH to the server and run `deploy/deploy.sh <service>` from the repo checkout
(pull, `uv sync`, collectstatic, restart the gunicorn service). Run migrations by hand if needed.
The systemd unit's `ExecStart` should use `<repo>/.venv/bin/gunicorn`.

`requirements.txt` is a pinned export of the lockfile for anywhere that only has pip.
