# Degen X Poster v1.0.5

Admin verifies slips, sends Telegram, then posts the same cards to X from the queue. Nothing auto-posts.

## Setup

1. `chrome://extensions` → Reload **Degen X Poster** (v1.0.5)
2. Logged into **x.com** in the same Chrome profile
3. Deployed site with `/admin/x-queue.json` (latest webapp)

## Daily workflow

1. Admin → **Send to Telegram** — verify Today's Picks / combos, then send
2. Admin → **X / Twitter posts** — post the same cards when you want
3. Settled RESULT slips: **Telegram results** → Post due results to Telegram + X

## Notes

- One Telegram send → one X run per day (session guard prevents double-start on refresh)
- Extension must stay loaded; reload after code updates
