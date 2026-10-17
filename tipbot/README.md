# Markov tipper

A small ski-tipp bot. Pure Python with no dependencies; the trained model is `model.json` (about 11 KB) and a full backtest takes about 12 seconds.

## How it works

1. **Form, as a hidden Markov model.** Each racer has a hidden form state per discipline: dominant, podium, contender, solid or fringe. After each race the state moves by a transition matrix, and over the summer by a second one. Each result is an emission: win, 2-3, 4-10, 11-30, out (not in the results, e.g. no second run) or DNF. The parameters are shared per discipline and fitted with Baum-Welch. Each racer's belief is filtered forward race by race.
2. **Race.** Each starter gets a Plackett-Luce strength fitted on past podiums. It combines the predicted form, form in the sister discipline (SL/GS, SG/DH), the racer's own decayed win and podium rate (which separates an Odermatt from the rest of the top group), an Elo-style online rating, decayed World Cup points, venue form (how the racer usually does at this hill compared with elsewhere) and the start group. Podium chances follow exactly from those strengths. DNF chances come from the form model and start group.
3. **Tipp.** It picks the podium and DNF tipp with the highest *expected ski-tipp points*, using the scoring in `tipp_scorer.py`: start group multipliers, rank and podium bonuses, alle im Ziel. It skips the alleinsieger bonus, which depends on the other tippers.

## Backtest

`backtest` (the default rolling scheme) tipps each winter with a model trained only on earlier winters, as in real use. The points are standard + bonus per race, the same as the tippers' tallies.

| winter | bot | average tipper | best tipper | bot would have finished |
| --- | --- | --- | --- | --- |
| 2021/22 | 2.80 | 2.02 | 2.38 | 1st of 16 |
| 2022/23 | 2.64 | 2.43 | 2.74 | 4th of 16 |
| 2023/24 | 3.01 | 2.51 | 3.01 | 1st of 16 |
| 2024/25 | 2.52 | 1.90 | 2.28 | 1st of 17 |
| 2025/26 | 2.23 | 2.10 | 2.62 | 5th of 15 |

`backtest --scheme loo` leaves one winter out and trains on all the others, including later ones. That tests 2019/20 and 2020/21 too and gives every fold more data. The shared parameters have then seen the future, but racer form is still filtered in date order. It agrees with the rolling scheme: the bot finishes 1st in 4 of 7 winters. The exception is 2019/20, where it's last, because that's where the data starts and the bot knows nothing about anyone yet.

World champs and Olympics count towards their winter, but nation quotas keep regulars out, so absence there isn't read as "out". They're the bot's weak spot: over 16–20 championship races it scores about the same as the average tipper (2.0–2.1), and the backtest prints this separately.

Per-race points are noisy (standard deviation about 1.8), so a single season's average can be off by ±0.55 on luck alone. Read the table as "consistently above the average tipper", not as a precise ranking.

### What else was tried

All runs used the same five-winter backtest. Methods were compared on podium log-likelihood, which is much less noisy than points, and on paired per-race points against the previous version.

| change | podium log-likelihood per race | points per race |
| --- | --- | --- |
| Markov form only (first version) | baseline | baseline |
| + decayed World Cup points | +0.07 | +0.05 ± 0.06 |
| + Elo-style rating | +0.03 | +0.10 ± 0.07 |
| + venue form | +0.03 | -0.02 ± 0.08 |
| + experience (number of starts) | -0.01 | -0.09 ± 0.07 |
| + points, rating, venue, ridge 5 (**current**) | **+0.13** | **+0.22 ± 0.09** |
| decayed WC points instead of the Markov form | +0.06 | -0.03 ± 0.09 |
| Elo rating instead of the Markov form | -0.11 | -0.03 ± 0.11 |
| gradient-boosted trees on the same features | -0.07 vs current | |
| + short-term momentum (last race or two) | +0.00 vs current | -0.01 ± 0.03 |
| logistic regression on the same features | +0.00 vs current | |

The Markov model and the simple ratings carry different information, and the combination beats either one. A nonlinear model doesn't help: with about 270 races, the trees overfit and a linear model is about as good as it gets. More form states (3 to 6), fitting on the top 5 or top 10 places instead of the podium, and other decay rates were all within noise.

### Tippers and big multipliers

Measured against the bot's out-of-sample odds, tippers' 6x picks landed 38 times where 21.5 were expected. 14 of 16 regular tippers made more from 3x+ picks than the odds predicted. The bot's own 3x/6x picks land as often as it expects, so the outsiders aren't under-priced as a group. The tippers know something about *which* outsiders (injuries, training, conditions, course fit) that results don't show. Racers the crowd picked beat their odds, and racers nobody picked fell short.

The bot can't use other tipps: they're hidden until the race starts, and reading them early would be peeking. Short-term momentum doesn't capture it either. Instead, `predict --boost 'NAME=FACTOR'` adds your own knowledge: a factor of 2 roughly doubles that racer's podium chance, and the bot recomputes the best tipp. That's where a 3x or 6x pick pays off.

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
uv run python tipbot/markov_tipper.py backtest [-v] [--scheme loo]
uv run python tipbot/markov_tipper.py train
uv run python tipbot/markov_tipper.py form --discipline SL
uv run python tipbot/markov_tipper.py predict --discipline GS --venue Soel --start-list start_list.csv --boost 'HAUGAN Timon=2'
uv run python tipbot/markov_tipper.py predict --race <fis race id> --race-db <db with its start list>
```

The start list csv is `bib,racer` per line, where racer is a FIS code or a name as FIS writes it (see `example_start_list.csv`). Bibs matter a lot, because they set the multipliers, so wait for the start list, which is usually published the afternoon before. Without one, `predict --discipline` guesses bibs from form, which is fine for a preview but not for a real tipp.
