# Google Search Console setup (degenpredictions.com)

Manual steps -- you need the Google account that owns the domain DNS / Cloudflare.

## 1. Add the property

1. Go to [Google Search Console](https://search.google.com/search-console).
2. Add property → choose **Domain** `degenpredictions.com` (preferred; covers http/https and www).
3. Verify ownership via DNS TXT record in **Cloudflare** (or the DNS host for the domain).
   - Paste the TXT record Google shows, wait a few minutes, click Verify.

If Domain verification is blocked, use **URL prefix** `https://degenpredictions.com/` and the HTML file or meta-tag method instead.

## 2. Submit the sitemap

1. In Search Console → **Sitemaps**.
2. Submit: `https://degenpredictions.com/sitemap.xml`
3. Confirm status becomes **Success** (may take hours).

The live app already serves `/sitemap.xml` and `/robots.txt` pointing at that sitemap.

## 3. Priority URLs to request indexing (after deploy)

Use **URL Inspection** → Request indexing for:

- `https://degenpredictions.com/`
- `https://degenpredictions.com/sports`
- `https://degenpredictions.com/accuracy`
- `https://degenpredictions.com/todays-picks`
- `https://degenpredictions.com/todays-picks/tennis/atp`
- `https://degenpredictions.com/todays-picks/cricket/ipl`
- `https://degenpredictions.com/todays-picks/cricket/cpl`
- `https://degenpredictions.com/todays-picks/baseball/mlb`
- `https://degenpredictions.com/sports/tennis`
- `https://degenpredictions.com/ipl/fixtures`
- `https://degenpredictions.com/cpl/fixtures`
- `https://degenpredictions.com/mlb/results`
- `https://degenpredictions.com/faq`
- `https://degenpredictions.com/crypto`
- `https://degenpredictions.com/stocks`

## 4. Off-site checklist (cannot be done in the webapp)

- [ ] Search Console property verified + sitemap submitted (above)
- [ ] Request indexing for priority URLs after each major content deploy
- [ ] Optional: create a real Telegram channel / WhatsApp broadcast — then paste the URL into the site (do **not** invent placeholders)
- [ ] Optional: post daily Today's Picks links on X using the in-page Share → X button
- [ ] Optional: Google Business / social bios linking to degenpredictions.com with brand name **Degen Predictions**

## 5. What to watch weekly

| Report | What good looks like |
|--------|----------------------|
| Performance | Impressions rising on Sports / Crypto / Stocks / tennis / IPL / CPL queries |
| Coverage / Pages | No spike of soft-404s on match URLs |
| Sitemaps | Discovered URL count roughly matching site size |

## 6. After Dubai/Metals demotion

Those URLs (`/dubai`, `/metals`) still return 200 so bookmarks do not break. If Search Console later shows them eating crawl budget with little traffic, add `noindex` on those templates in a follow-up change -- do not 404 them without redirects.
