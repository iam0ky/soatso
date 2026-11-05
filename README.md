# Solar dashboard (Deye Cloud) — GitHub version

Runs every day at **8 AM, 2 PM and 8 PM IST** on GitHub Actions and publishes the dashboard
on GitHub Pages.

| What | Where it lives | Who can see it |
|---|---|---|
| Deye login, alert keys | GitHub **Secrets** | Nobody (encrypted, hidden in logs) |
| `solar_history.csv`, `strings_history.json` | Private **Actions cache** (not in the repo) | Only this workflow |
| Dashboard `docs/index.html` | Repo + Pages | **Anyone with the link** (the charts include your daily units) |
| Run logs | Actions tab | Public, with plant name and IDs masked |

## One-time setup

1. **Create a public repository** and upload everything in this folder
   (keep `.github/workflows/solar.yml` exactly where it is).
   Do **not** put any passwords in `solar_chart.py`.
2. **Secrets** — Settings → Secrets and variables → Actions → *Secrets* → New repository secret:
   - `DEYE_APP_ID`, `DEYE_APP_SECRET`
   - `DEYE_EMAIL` (or `DEYE_MOBILE`), `DEYE_PASSWORD`
   - optional alerts: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `FAST2SMS_API_KEY`, `FAST2SMS_NUMBERS`
3. **Variables** (optional) — same page → *Variables* tab:
   - `DISPLAY_NAME` — heading shown on the page (defaults to "My Solar", not your Deye plant name)
   - `THEME` — `clean` / `leaf` / `night` / `sunny`
   - `STRING_PANELS` — panels in series per string, e.g. `{"PV1": 15, "PV2": 15}`
   - `PARALLEL_STRINGS` — strings per input, e.g. `{"PV1": 2, "PV2": 2}`
   - `PANEL_WATTS` — e.g. `620`
   - `PANEL_VOLTS_HOT` — one panel's hot-midday volts, e.g. `39` for 600–630 W panels
4. **Workflow permissions** — Settings → Actions → General:
   - *Workflow permissions*: **Read and write permissions** → Save
   - *Approval for running fork pull request workflows*: **Require approval for all external contributors**
5. **First run** — Actions tab → *Solar dashboard* → **Run workflow** (takes a few minutes).
6. **Turn on Pages** — Settings → Pages → *Deploy from a branch* → `main` / `/docs` → Save.
   Your dashboard: `https://<username>.github.io/<repo-name>/`

## Morning update for residents
The 8 AM run writes a friendly message: yesterday's units, today's weather outlook
for the rooftop (free forecast from Open-Meteo) with expected units, this month's
total and a closing line. It is sent once per day:
- **To a Telegram channel** — set Variable `RESIDENT_TELEGRAM_CHAT` (e.g. `@sumukhasolar`)
  and add your bot to that channel as an admin.
- **Or to you, for WhatsApp** — leave it blank: you get the message on Telegram plus a
  one-tap link that opens WhatsApp with the text filled in; pick the residents' group and send.
The dashboard also shows it with **Share on WhatsApp** and **Copy text** buttons.
Needs secrets `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`. Optional Variables:
`COMMUNITY_NAME` (e.g. `Sumukha Opal`), `DASHBOARD_URL` (your Pages link), `LATITUDE`, `LONGITUDE`.

## Inverter alerts
Each run also reads the inverter's own fault/warning log from Deye (last 30 days) and
lists it on the dashboard with the cause and suggested fix. An alert that is still active
turns the health card amber (warning) or red (fault) and sends you a Telegram alert.
The log is kept in the private cache, not in this repo.

## Notes
- Scheduled runs can start 5–30 minutes late when GitHub is busy.
- If you change the times, also update `RUN_TIMES` in the workflow (IST) so the page's
  "Next update around …" line stays right.
- To change the times, edit the `cron:` lines in `.github/workflows/solar.yml` (UTC; IST = UTC + 5:30).
  e.g. 9 AM IST = `30 3 * * *`, 3 PM IST = `30 9 * * *`, 8 PM IST = `30 14 * * *`.
- If the cache is ever cleared, the next run re-downloads history from Deye automatically.
- Want a password later? Add a `DASHBOARD_PASSWORD` secret, pass it in the workflow's `env`,
  and add `cryptography` to `requirements.txt` — the script supports it.
