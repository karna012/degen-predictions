# Daily distribution (Today's Picks → digest → drafts / optional Telegram)

Thin automation over **existing** Today's Picks and Best Combos. No parallel pick model.

## Live pages

- Digest: https://degenpredictions.com/todays-picks/digest
- Today's Picks: https://degenpredictions.com/todays-picks
- Best Combos: https://degenpredictions.com/todays-picks/combos

On-site **Join Telegram** CTAs use `TELEGRAM_CHANNEL_URL` (public invite). That invite is **not** the bot `chat_id`.

## What the daily job does

1. Loads the same high-probability legs as the site (`_gather_combo_picks_today`).
2. Selects Top-3 by confidence (same cards as Today's Picks).
3. Always writes:
   - `data/daily_digest/YYYY-MM-DD.json`
   - `output_tables/daily_drafts/YYYY-MM-DD.md` (X / Quora / Shorts / Telegram)
4. Does **not** post Telegram or X unless `CHANNEL_AUTO_POST=1`. Admin verifies on `/admin` then sends.
5. Optional X / email only when you send from admin (or if auto-post is explicitly enabled).
6. Idempotent per IST day (`data/daily_digest/state.json`).

```bash
bash deploy/daily_distribute.sh
bash deploy/daily_distribute.sh --dry-run
bash deploy/daily_distribute.sh --force-repost
```

## Schedule (~09:00 IST)

Timer: `degen-daily-distribute.timer` at **03:30 UTC** (09:00 IST), plus catch-up runs at **05:00 UTC** (10:30 IST), **07:30 UTC** (13:00 IST), and **10:00 UTC** (15:30 IST). The job **writes the digest for admin** — it does **not** auto-post Telegram or X unless `CHANNEL_AUTO_POST=1`.

**Admin send:** `/admin?section=telegram` — tick Telegram and/or X, then send Today's Picks and/or featured combos. X cards land on `/admin?section=twitter`. Settled RESULT slips: `/admin?section=degen_team` — tick Telegram and/or X → **Send due results**.

`degen-post-event.timer` every 5 minutes **monitors** fixtures and **settles** finished games into the admin queue. It does not post to Telegram/X.

```bash
sudo cp deploy/systemd/degen-daily-distribute.service /etc/systemd/system/
sudo cp deploy/systemd/degen-daily-distribute.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now degen-daily-distribute.timer
systemctl list-timers 'degen-daily-distribute*' --no-pager
```

Or: `deploy/setup_automation.sh`

## Telegram setup (BotFather) — ~2 minutes

Invite links like `https://t.me/+....` are for humans joining. Auto-post needs a **bot** + numeric **chat_id**.

1. Open Telegram → search **@BotFather** → `/newbot` → copy the token.
2. Open your channel → **Administrators** → **Add Admin** → select your bot → enable **Post Messages**.
3. Get `TELEGRAM_CHAT_ID` (usually starts with `-100`):
   - Forward any channel post to **@userinfobot**, or
   - Post something in the channel, then call  
     `https://api.telegram.org/bot<TOKEN>/getUpdates`  
     and read `channel_post.chat.id`.
4. On EC2, edit `deploy/production.env` (never commit this file):

```bash
TELEGRAM_BOT_TOKEN=...          # from BotFather
TELEGRAM_CHAT_ID=-100...        # numeric; NOT the t.me/+ invite
TELEGRAM_CHANNEL_URL=https://t.me/+...   # public invite for site CTAs
```

5. Restart / re-run:

```bash
sudo systemctl restart degen-predictions   # pick up env for web CTAs if loaded there
bash deploy/daily_distribute.sh --force-repost
```

Without a bot token the job still writes drafts and logs:  
`skipped Telegram (set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID)`.

## Env vars

| Variable | Purpose |
|----------|---------|
| `TELEGRAM_CHANNEL_URL` | Public invite on homepage / Today's Picks / digest CTAs |
| `TELEGRAM_BOT_TOKEN` | BotFather token (used when **you** send from `/admin`) |
| `TELEGRAM_CHAT_ID` | Numeric `-100…` or `@publicchannel` |
| `CHANNEL_AUTO_POST` | Leave unset/`0`. `1` restores old timer auto-send |
| `TWITTER_*` / `X_*` | Optional X API (admin RESULT button); otherwise X queue / extension |
| `SMTP_*` / `DIGEST_EMAIL_TO` | Optional email |

See `deploy/production.env.example`.

## Code map

| Piece | Path |
|-------|------|
| Job + Telegram post | `webapp/daily_digest.py` |
| Shell | `deploy/daily_distribute.sh` |
| Timer | `deploy/systemd/degen-daily-distribute.{service,timer}` |
| Digest page | `webapp/templates/digest.html` |
| CTA partial | `webapp/templates/partials/telegram_cta.html` |
