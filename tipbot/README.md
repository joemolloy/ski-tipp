# Markov tipper

A small ski-tipp bot. Pure Python with no dependencies; the trained model is `model.json` (about 11 KB) and a full backtest takes about 35 seconds.

## How it works

1. **Form, as a hidden Markov model.** Each racer has a hidden form state per discipline: dominant, podium, contender, solid or fringe. After each race the state moves by a transition matrix, and over the summer by a second one. Each result is an emission: win, 2-3, 4-10, 11-30, out (not in the results, e.g. no second run) or DNF. The parameters are shared per discipline and fitted with Baum-Welch. Each racer's belief is filtered forward race by race.
2. **Race.** Each starter gets a Plackett-Luce strength fitted on past podiums. It combines the predicted form, form in the sister discipline (SL/GS, SG/DH), the racer's own decayed win and podium rate (which separates an Odermatt from the rest of the top group), an Elo-style online rating, decayed World Cup points, venue form (how the racer usually does at this hill compared with elsewhere), age, the start group, and for speed races the event's training runs (best rank, best gap to the fastest, skipped training). Podium chances follow exactly from those strengths. DNF chances come from the form model, the racer's own DNF rate, the discipline, how often the first 30 DNF on this course, and the start group. Alle im Ziel has its own race-level model, because multiplying thirty finish chances compounds small errors.
3. **Tipp.** It picks the podium and DNF tipp with the highest *expected ski-tipp points*, using the scoring in `tipp_scorer.py`: start group multipliers, rank and podium bonuses, alle im Ziel. It only picks racers the tipp form offers, which is the World Cup standings: racers with WC points this winter or last. It skips the alleinsieger bonus, which depends on the other tippers.

## Backtest

`backtest` (the default rolling scheme) tipps each winter with a model trained only on earlier winters, as in real use. The points are standard + bonus per race, the same as the tippers' tallies.

| winter | bot | average tipper | best tipper | bot would have finished |
| --- | --- | --- | --- | --- |
| 2019/20 | 2.00 | 2.33 | 2.96 | 14th of 16 |
| 2020/21 | 2.21 | 2.12 | 2.70 | 6th of 16 |
| 2021/22 | 2.38 | 2.02 | 2.38 | 1st of 16 |
| 2022/23 | 2.73 | 2.43 | 2.74 | 3rd of 16 |
| 2023/24 | 2.73 | 2.51 | 3.01 | 4th of 16 |
| 2024/25 | 2.80 | 1.90 | 2.28 | 1st of 17 |
| 2025/26 | 2.48 | 2.10 | 2.62 | 2nd of 15 |

Each winter is scored under the rules of its time, which reproduce all 3,955 stored tallies exactly. Up to 2020/21, alle im Ziel and a right 2nd or 3rd place were worth 1 instead of 0.5. Up to 2021/22, the speed favourites were seeded onto odd bibs, which were the 1x group. The bot is weakest in those old-rules winters, where it also has the least training data.

`backtest --scheme loo` leaves one winter out and trains on all the others, including later ones. That gives every fold more data. The shared parameters have then seen the future, but racer form is still filtered in date order. From 2019/20 it places 7th, 10th, 1st, 6th, 3rd, 1st, 3rd.

Early in a winter the bot does at least as well as later. Its edge over the average tipper is largest in each discipline's first two races, because the summer transition and last season's ratings carry over. The real cold start is where the data begins, which is why the 2016/17–2018/19 warm-up seasons were added. They lift 2019/20 by several places.

World champs and Olympics count towards their winter, but nation quotas keep regulars out, so absence there isn't read as "out". They're the bot's weak spot: over 20 championship races it scores about the same as the average tipper (2.0–2.1), and the backtest prints this separately.

2019/20 and 2020/21 have one quirk: the database didn't keep DNFs from bib 31+, so a late-bib DNF pick couldn't score. The tippers were scored on the same data, so the comparison is fair, but the bot's expected points run high for those winters.

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
| only pick racers in the WC standings (a fix, not a feature) | | -0.06 |
| + own DNF rate and discipline in the DNF model | | +0.06 |
| + warm-up seasons 2016/17–2018/19 | | +0.00 ± 0.06 (2019/20: last → 8th) |
| canonical venue names | | +0.02 ± 0.02 |
| + speed training runs (on speed races with training) | +0.16 | -0.01 ± 0.13 |
| + home race | -0.00 | -0.04 ± 0.02 (dropped) |
| + age | +0.02 ± 0.02 | +0.07 ± 0.05 |
| retuned ridge, decays, rating step | | all within ±0.04, kept as they were |
| scoring and seeding by era (a fix) | | old-rules winters drop, e.g. 2019/20 to 13th |
| recency weighting, half-life 4 winters | +0.03 ± 0.01 | +0.12 ± 0.07 |
| random forest / neural net / ensembles as the learner | -0.20 / -1.44 / -0.04 | -0.17 / -0.75 / -0.11 (vs Plackett-Luce) |
| missed races count as no information, not "out" (injury) | -0.02 | -0.03 ± 0.06 (kept "out") |
| + venue DNF rate (DNF log-likelihood) | +0.07 ± 0.07 | -0.02 ± 0.01 |
| + race-level alle im Ziel model | speed log-loss -0.575 → -0.516 | +0.02 ± 0.01 |
| + venue-weighted SG/DH link (venue form and this weekend's sister race) | -0.01 ± 0.02 | -0.04 ± 0.04 (removed) |
| race-day weather (Open-Meteo archive, `fetch_weather.py`) vs late-bib luck and DNFs | no correlation beyond noise in 277 races | not used |
| + the crowd's recent picks per discipline (lagged: past races' published tipps), and picks beyond form | +0.02 ± 0.015 | -0.01 ± 0.05 (not kept) |

The Markov model and the simple ratings carry different information, and the combination beats either one. A nonlinear model doesn't help: with a few hundred races, the trees overfit and a linear model is about as good as it gets. More form states (3 to 6), fitting on the top 5 or top 10 places instead of the podium, and other decay rates were all within noise.

The biggest single source of the bot's edge is the DNF pick. In tech races it earns about 0.6 points per race against 0.27 for the average tipper, by picking late-starting racers who are in the standings but DNF often. Tippers mostly pick DNFs from the first 30 bibs.

Training runs matter most for newcomers. In 2025/26 the tippers beat the bot clearly in downhill (2.94 vs 2.10 per race), mostly through Franzoni: 3rd from bib 28 in Wengen, then a win in Kitzbühel. He'd been fastest in Kitzbühel training. With training runs, 2025/26 went from 9th to 4th.

### What limits the bot

Analysed over the four current-rules winters, 2022/23–2025/26:

- **Model choice isn't the limit.** Gradient-boosted trees and logistic regression on the same features do no better than Plackett-Luce.
- **Data quantity isn't either.** Fitting the race model on only the last 3–6 winters is as good as using all ten. Older winters mislead a little, because racing and the FIS seeding drift. Weighting recent winters more (an older winter counts half as much every 4 winters) improves podium and DNF forecasts and adds about +0.12 points per race. The DNF model is the exception and needs several winters.
- **Features carry most of the gain.** In order of what removing them costs: training runs, age, decayed WC points, the Markov form, venue form. The Elo rating, own record, sister discipline and start groups add nothing on top.
- **The rules are mostly exploited.** Expected-points tipping is worth +0.17 per race over picking the favourites. What's left is the alleinsieger bonus, which needs the other tipps.

How the best tippers differ: over these four winters the bot averages 2.69 per race and the best tipper (Donkunho) 2.57, but they win in different ways. Donkunho makes about 0.2 per race more on podium picks. He mostly goes with the crowd (62% of his picks are shared), differs from the bot's three favourites by about one pick per race, and scores 0.3 per race more than the bot's odds give his tipps credit for. The bot makes about 0.3 per race more on DNF picks. More multipliers isn't the answer: the tipper with the most 3x+ picks (36%) ranks near the bottom.

So the best use for a person is a hybrid: their own podium tipp plus the bot's DNF pick. That adds 0.2–0.3 per race for every regular tipper, and would put the best one at 2.86.

Two things that sound important turned out not to be:

- **Strong racers on late bibs.** Every bib 31+ podium in ten winters came from a breakthrough racer with 0–1 previous podiums (Strolz, Cater, Pinheiro Braathen, McGrath). Racers with 3+ career podiums started from bib 31+ 205 times, mostly outside their usual discipline, and never made the podium. So scaling late starters down as a group does no harm.
- **Course-specific SG/DH links.** They're real (SG/DH correlation 0.5 at Lake Louise and Kvitfjell, 0.32 at Beaver Creek), but the bot's per-discipline venue form, decayed points and training runs already cover it.

Alle im Ziel does vary by course (in the first 30, everyone finished 4 of 6 Garmisch downhills but only 2 of 16 at Bormio). With about 90 speed races, though, a calibrated race-level model only matches the long-run 21% rate. It doesn't beat it.

### Winning the season

A season simulator entered the bot as one more tipper in each past season. It started from the stored tallies (reproducing the real leaderboards exactly), recomputed the alleinsieger with the bot included, and applied each race's points multiplier and the points adjustments. The plain bot would have won 3 of the 7 World Cup seasons (2021/22, 2022/23, 2024/25), and come 2nd in 2025/26 and 3rd in 2023/24. Championships, with only 5 races each, go to whoever lands a big multiplier, and the bot finishes mid-field.

Two ways of playing for the win instead of the race, both judged on the chance of winning over 400 resampled versions of each season:

- **Alleinsieger-aware tipping.** A crowd model learned from past tipps simulates what rivals pick, and the bot adds the expected sole-best bonus to each tipp. World Cup total 700.7 vs 701.7 for the plain bot. The bonus is too rare to be worth giving up expected points.
- **Risk by standings.** Expected points plus λ × spread, with λ set by the gap to the leader and the races left. Gambling when behind lowers the chance of winning (31% → 27–30%), and protecting a lead doesn't raise it (29–31%). Variance from the race results swamps what the tipp choice can add.

So plain expected-points tipping is also the best way to win the season.

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

Do this after each race weekend so the form is current.

`data/fis.sqlite3` (also git-ignored) is scraped from FIS and kept separate, so refreshing the snapshot never wipes it. It holds the warm-up seasons 2016/17–2018/19, which `fetch_season.py` imports with the app's own scrapers, and the extras from `fetch_extras.py`: speed training runs and racers' birth years. The bot loads it automatically (`--no-extra` to leave it out):

```
export DB_LOCATION=tipbot/data/fis.sqlite3 DJANGO_SECRET_KEY=dev
uv run manage.py migrate
uv run python tipbot/fetch_season.py "2018/2019" 2019 WC
uv run python tipbot/fetch_season.py "World Champs 2019" 2019 WSC
uv run python tipbot/fetch_extras.py tipbot/data/history.sqlite3 tipbot/data/fis.sqlite3 tipbot/data/fis.sqlite3
```

Championship seasons must have "Champs", "Olympic" or "Games" in their name. `fetch_extras.py` skips races it has already done, so rerun it after a weekend to add the new races' training runs. Both scripts space out their requests and wait out FIS's rate limit. Don't run them at the same time on the same file.

Known gaps in the data, which the model works around:
- Tech races store second-run finishers and DNFs, but not racers who missed the second run.
- 2019/20 and 2020/21 don't store DNFs from bib 31 and later.

Because of these, the bot doesn't trust its per-racer numbers for bib 31+. It scales them so they win podium places at the historical rate.

## Tipp-Assist

The Tipp-Assist page, `/app/tipp-assist/` (Tipp-Assist in the top bar), shows the bot's tipps for the next 3, 6 or 10 races: the podium and DNF pick with bibs and multipliers, expected points, the main contenders' odds, and your own current tipp.

- **Use this tipp** saves the bot's tipp as yours, through the same rules as the tipp form: only before the start, and only racers the form offers.
- **Not starting** takes racers (autocomplete) who are injured or skipping. They're left out of every prediction until you remove them.
- **Rerun** fetches this week's training runs from FIS for upcoming speed races, then recomputes.
- Until the start list is out, bibs are guessed from form (flagged "bibs guessed").

Only members of the **Tipp-Assist** group can open it; everyone else gets a 404. The page reads the live database directly, so results imported through the app count straight away. It runs in about half a second, so there's nothing to schedule.

Server setup, once:

1. Deploy as usual. There's no migration.
2. In Admin → Groups, add a group called `Tipp-Assist` and put your user in it.
3. Keep Tipp-Assist's data next to the database, outside the repo, so a re-clone can't wipe it. Create the folder and copy the scraped history up. Without the history, the bot still works but loses the warm-up seasons, training runs and ages:
   ```
   ssh -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem bitnami@63.179.212.78 'mkdir -p /opt/bitnami/projects/db/tipp-assist'
   scp -i ~/.ssh/LightsailDefaultKey-eu-central-1.pem tipbot/data/fis.sqlite3 bitnami@63.179.212.78:/opt/bitnami/projects/db/tipp-assist/
   ```
4. Point the app at it: add `TIPP_ASSIST_DATA=/opt/bitnami/projects/db/tipp-assist` to the server's `.env`, then restart (`sudo systemctl restart gunicorn`, or deploy).

The page keeps its state (the not-starting list and fetched training runs) in `tipp_assist_state.json` in that folder. The server process writes it when you click Save or Rerun, so the user gunicorn runs as needs write access to the folder (`systemctl show -p User gunicorn`). The model's weights come from `model.json` in git: retrain locally now and then, then commit and deploy.

## Use

```
uv run python tipbot/markov_tipper.py backtest [-v] [--scheme loo]
uv run python tipbot/markov_tipper.py train
uv run python tipbot/markov_tipper.py form --discipline SL
uv run python tipbot/markov_tipper.py predict --discipline GS --venue Soelden --start-list start_list.csv --boost 'HAUGAN Timon=2'
uv run python tipbot/markov_tipper.py predict --discipline DH --venue Kitzbuehel --start-list start_list.csv --fis-race <fis race id>
uv run python tipbot/markov_tipper.py predict --race <fis race id> --race-db <db with its start list>
```

The start list csv is `bib,racer` per line, where racer is a FIS code or a name as FIS writes it (see `example_start_list.csv`). Bibs matter a lot, because they set the multipliers, so wait for the start list, which is usually published the afternoon before. Without one, `predict --discipline` guesses bibs from form, seeded the FIS way, which is fine for a preview but not for a real tipp. For speed races, `--fis-race` fetches that race's training runs from FIS, so run it after the last training.
