# Markov tipper

A small ski-tipp bot. Pure Python with no dependencies; the trained model is `model.json` (about 11 KB) and a full backtest takes about 12 seconds.

## How it works

1. **Form, as a hidden Markov model.** Each racer has a hidden form state per discipline: dominant, podium, contender, solid or fringe. After each race the state moves by a transition matrix, and over the summer by a second one. Each result is an emission: win, 2-3, 4-10, 11-30, out (not in the results, e.g. no second run) or DNF. The parameters are shared per discipline and fitted with Baum-Welch. Each racer's belief is filtered forward race by race.
2. **Race.** Each starter gets a Plackett-Luce strength fitted on past podiums. It combines the predicted form, form in the sister discipline (SL/GS, SG/DH), the racer's own decayed win and podium rate (which separates an Odermatt from the rest of the top group) and the start group. Podium chances follow exactly from those strengths. DNF chances come from the form model and start group.
3. **Tipp.** It picks the podium and DNF tipp with the highest *expected ski-tipp points*, using the scoring in `tipp_scorer.py`: start group multipliers, rank and podium bonuses, alle im Ziel. It skips the alleinsieger bonus, which depends on the other tippers.

## Backtest

Each winter is tipped by a model trained only on earlier winters. The points are standard + bonus per race, the same as the tippers' tallies.

| winter | bot | average tipper | best tipper | bot would have finished |
| --- | --- | --- | --- | --- |
| 2021/22 | 2.46 | 2.02 | 2.38 | 1st of 16 |
| 2022/23 | 2.51 | 2.43 | 2.74 | 7th of 16 |
| 2023/24 | 2.77 | 2.51 | 3.01 | 3rd of 16 |
| 2024/25 | 2.16 | 1.90 | 2.28 | 2nd of 17 |
| 2025/26 | 2.21 | 2.10 | 2.62 | 5th of 15 |

Per-race points are noisy (standard deviation about 1.8), so a single season's average can be off by ±0.55 on luck alone. Read the table as "consistently above the average tipper", not as a precise ranking.

## Data

`data/history.sqlite3` (git-ignored) is a snapshot of the live database. It holds every season since 2019/20, with the world champs, Olympics and everyone's tipps. To refresh it, take a consistent copy on the server and pull it down:

```
ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem bitnami@63.179.212.78 'sqlite3 /opt/bitnami/projects/db/ski-tipp-db.sqlite3 ".backup /tmp/ski-tipp-latest.sqlite3"'
scp -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem bitnami@63.179.212.78:/tmp/ski-tipp-latest.sqlite3 tipbot/data/history.sqlite3
uv run python tipbot/markov_tipper.py train
```

Do this after each race weekend so the form is current. `fetch_season.py` can scrape a season straight from FIS into a copy instead, which is useful when the live database is missing results:

```
DB_LOCATION=tipbot/data/history.sqlite3 DJANGO_SECRET_KEY=dev uv run python tipbot/fetch_season.py "2025/2026" 2026 WC
```

Known gaps in the data, which the model works around:
- Tech races store second-run finishers and DNFs, but not racers who missed the second run.
- 2019/20 and 2020/21 don't store DNFs from bib 31 and later.

Because of these, the bot doesn't trust its per-racer numbers for bib 31+. It scales them so they win podium places at the historical rate.

## Use

```
uv run python tipbot/markov_tipper.py backtest [-v]
uv run python tipbot/markov_tipper.py train
uv run python tipbot/markov_tipper.py form --discipline SL
uv run python tipbot/markov_tipper.py predict --discipline GS --start-list start_list.csv
uv run python tipbot/markov_tipper.py predict --race <fis race id> --race-db <db with its start list>
```

The start list csv is `bib,racer` per line, where racer is a FIS code or a name as FIS writes it (see `example_start_list.csv`). Bibs matter a lot, because they set the multipliers, so wait for the start list, which is usually published the afternoon before. Without one, `predict --discipline` guesses bibs from form, which is fine for a preview but not for a real tipp.
