# -*- coding: utf-8 -*-
"""
Builds the full Degen Predictions documentation as a .docx file.
Run: python build_document.py
Output: docs/Degen_Predictions_Complete_Documentation.docx
"""
import os
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

HERE = os.path.dirname(os.path.abspath(__file__))
DIAGRAMS = os.path.join(HERE, "diagrams")
OUT_PATH = os.path.join(HERE, "Degen_Predictions_Complete_Documentation.docx")

NAVY = RGBColor(0x0A, 0x0E, 0x27)
GOLD = RGBColor(0xC9, 0xA9, 0x61)
CYAN = RGBColor(0x00, 0x8C, 0xB3)
DARKGREY = RGBColor(0x33, 0x33, 0x33)

doc = Document()

# ---- base style ----
style = doc.styles["Normal"]
style.font.name = "Calibri"
style.font.size = Pt(11)


def set_cell_shading(cell, color_hex):
    shd = OxmlElement('w:shd')
    shd.set(qn('w:fill'), color_hex)
    cell._tc.get_or_add_tcPr().append(shd)


def add_title_page():
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for _ in range(6):
        doc.add_paragraph()
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("DEGEN PREDICTIONS")
    run.font.size = Pt(40)
    run.font.bold = True
    run.font.color.rgb = GOLD

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run("Complete Project Documentation")
    r.font.size = Pt(20)
    r.font.color.rgb = CYAN

    sub2 = doc.add_paragraph()
    sub2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = sub2.add_run("How the website works, end to end -- written so anyone can follow it")
    r2.font.size = Pt(13)
    r2.italic = True

    doc.add_paragraph()
    tagline = doc.add_paragraph()
    tagline.alignment = WD_ALIGN_PARAGRAPH.CENTER
    rt = tagline.add_run("ML & AI-Powered Predictions Across Sports, Crypto, Stocks, Real Estate & Metals")
    rt.font.size = Pt(11)
    rt.font.color.rgb = DARKGREY
    doc.add_page_break()


def heading(text, level=1):
    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = NAVY
    return h


def para(text, size=11, bold=False, italic=False, color=None, space_after=8):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    if color:
        r.font.color.rgb = color
    p.paragraph_format.space_after = Pt(space_after)
    return p


def bullet(text, size=11):
    p = doc.add_paragraph(style="List Bullet")
    r = p.add_run(text)
    r.font.size = Pt(size)
    return p


def numbered(text, size=11):
    p = doc.add_paragraph(style="List Number")
    r = p.add_run(text)
    r.font.size = Pt(size)
    return p


def add_image(filename, width_in=6.3, caption=None):
    path = os.path.join(DIAGRAMS, filename)
    doc.add_picture(path, width=Inches(width_in))
    last_paragraph = doc.paragraphs[-1]
    last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if caption:
        cap = doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = cap.add_run(caption)
        r.font.size = Pt(9.5)
        r.italic = True
        r.font.color.rgb = DARKGREY


def add_table(headers, rows, col_widths=None):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr_cells = table.rows[0].cells
    for i, h in enumerate(headers):
        hdr_cells[i].text = ""
        p = hdr_cells[i].paragraphs[0]
        r = p.add_run(h)
        r.font.bold = True
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        set_cell_shading(hdr_cells[i], "1A2550")
    for row in rows:
        cells = table.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = str(val)
            for p in cells[i].paragraphs:
                for r in p.runs:
                    r.font.size = Pt(10)
    return table


# =============================================================================
# TITLE PAGE
# =============================================================================
add_title_page()

# =============================================================================
# TABLE OF CONTENTS (manual, simple)
# =============================================================================
heading("Table of Contents", level=1)
toc_items = [
    "1. What Is Degen Predictions? (Plain-English Overview)",
    "2. The Five Categories -- What Each One Does",
    "3. System Architecture -- How the Website Is Built",
    "4. How a Prediction Actually Gets Made",
    "5. The Machine Learning Models Used",
    "6. AWS -- Where the Website Lives and How It's Deployed",
    "7. Automation -- What Runs Automatically, Where, and When",
    "8. Incident History -- What Went Wrong and How It Was Fixed",
    "9. Mobile & Design Fixes -- A Running Log",
    "10. Live Market Data -- Real Prices From TradingView & Yahoo Finance",
    "11. Crypto & Stocks -- Live Charts and the Click-to-Sync Feature",
    "12. SEO, AEO & GEO -- How the Site Gets Found",
    "13. Version Control -- GitHub, Licensing, and Repository History",
    "14. Second Bug-Fix Wave -- Sticky Header, Widget Overlap, and More",
    "15. Glossary -- Plain-English Definitions of Every Technical Term Used",
]
for item in toc_items:
    bullet(item)
doc.add_page_break()

# =============================================================================
# SECTION 1
# =============================================================================
heading("1. What Is Degen Predictions? (Plain-English Overview)", level=1)
para(
    "Degen Predictions is a free website that uses Machine Learning (ML) and Artificial "
    "Intelligence (AI) to forecast how things are likely to perform in the future -- "
    "football and baseball matches, cryptocurrency prices, stock prices, Dubai real "
    "estate returns, and precious metal / gemstone prices."
)
para(
    "Think of it like a very well-read analyst that has studied years of history for each "
    "of these topics, and uses patterns from that history to make an educated guess about "
    "what happens next. It is not magic and it is not guaranteed to be right -- it is a "
    "structured, data-driven estimate, clearly labelled as such everywhere on the site."
)
para("In one sentence:", bold=True)
para(
    "“You pick a category, enter some details (like how much you want to invest, or which "
    "match you're curious about), and the website's model returns a forecast, calculated from "
    "real historical data.”",
    italic=True
)

# =============================================================================
# SECTION 2
# =============================================================================
heading("2. The Five Categories -- What Each One Does", level=1)
add_image("02_categories.png", caption="Figure 1 -- The five prediction categories on the site.")

heading("Sports", level=2)
para(
    "Predicts football (soccer) and baseball match outcomes: who wins, both-teams-to-score, "
    "total goals/runs over-under, corners, and more. Built on ELO-style team ratings plus a "
    "trained ML ensemble model, blended with head-to-head history and recent form."
)

heading("Crypto", level=2)
para(
    "Ranks cryptocurrencies by their AI-predicted return over the next 6 months, using market "
    "cap, price momentum, and volatility as inputs."
)

heading("Stocks", level=2)
para(
    "Same idea as crypto, but for US and Indian stock markets -- ranks companies by predicted "
    "6-month forward return using fundamentals (like P/E ratio) and price trends."
)

heading("Dubai Real Estate", level=2)
para(
    "A calculator: you enter an area, property type, bedrooms, investment amount, and how long "
    "you plan to hold the property (from 6 months up to 35 years). It returns a projected "
    "capital appreciation, rental income, and total ROI."
)
para(
    "This one has a special ingredient: an “infrastructure development multiplier.” Areas "
    "with new metro stations, malls, roads, and other construction projects get a growth boost "
    "in the model, because real Dubai development (new metro lines, Creek Harbour, Jebel Ali "
    "expansion, etc.) measurably accelerates property values nearby. This multiplier fades "
    "after about 10 years, once an area matures and the construction boom settles down."
)

heading("Metals", level=2)
para(
    "A calculator for precious metals (gold, silver, platinum, palladium) and industrial metals "
    "(copper, aluminum, steel, zinc, nickel, lithium). Enter an investment amount and a holding "
    "period (6 months to 25 years); it returns a forecasted price and total return, adjusted by "
    "a market-cycle multiplier based on how volatile that metal historically is. A live "
    "TradingView candlestick chart sits above the calculator, switching to whichever metal is "
    "currently selected."
)
para(
    "Gemstones (diamond, ruby, emerald, sapphire) were removed from this category entirely -- "
    "unlike metals, gemstones have no single quoted market price (a \"1-carat diamond\" varies "
    "100x in price by cut/color/clarity grading), so there was no way to source real, verifiable "
    "current pricing for them. Rather than show an invented number, the category was simplified "
    "to only what has real, checkable data behind it.",
    italic=True
)

doc.add_page_break()

# =============================================================================
# SECTION 3
# =============================================================================
heading("3. System Architecture -- How the Website Is Built", level=1)
para(
    "Every part of the site is built from a small number of moving pieces. Here is the full "
    "picture, from the moment someone opens degenpredictions.com in a browser to the moment "
    "they see a result on screen."
)
add_image("01_architecture.png", caption="Figure 2 -- Full request path from browser to server and back.")

para("Plain-English walkthrough:", bold=True)
numbered("A visitor types degenpredictions.com into their phone or laptop browser.")
numbered(
    "Cloudflare receives the request first. It handles the domain name lookup, provides the "
    "padlock (HTTPS/security), and shields the real server from direct attacks."
)
numbered(
    "Cloudflare forwards the request to the AWS EC2 server -- a single always-on virtual "
    "computer rented from Amazon Web Services."
)
numbered(
    "On that server, a program called gunicorn (a production-grade Python web server) receives "
    "the request and hands it to the Flask application (app.py) -- this is the “brain” of the site."
)
numbered(
    "Flask reads whatever data it needs -- prediction CSV/JSON files, trained ML model files, "
    "or HTML templates -- from the server's own disk, builds the finished web page, and sends "
    "it back the same way it came."
)
para(
    "Nothing in this chain calls back to your personal computer while a visitor is browsing -- "
    "the server is fully self-contained and serves whatever data was placed on it during the "
    "last deployment (see Section 6)."
)

doc.add_page_break()

# =============================================================================
# SECTION 4
# =============================================================================
heading("4. How a Prediction Actually Gets Made", level=1)
para(
    "Every category -- sports, crypto, stocks, real estate, metals -- follows the exact same "
    "5-step recipe under the hood, just with different data and different models."
)
add_image("03_prediction_pipeline.png", caption="Figure 3 -- The 5-step recipe behind every prediction on the site.")

add_table(
    headers=["Step", "What Happens", "Example (Metals)"],
    rows=[
        ("1. Fetch Data", "Pull the latest real-world numbers", "Latest gold, silver, lithium spot prices"),
        ("2. Train Model", "Update the ML model with the newest history", "Re-fit appreciation-rate curve per metal"),
        ("3. Predict", "The model produces a forecast", "“Gold: 8.5% annual appreciation, Growth cycle, 1.2x”"),
        ("4. Store Result", "Save the forecast to a file on disk", "A row in a prices/predictions CSV or JSON file"),
        ("5. Serve", "Flask reads that file and renders it as a webpage", "The Price Forecast panel on /metals"),
    ]
)

doc.add_page_break()

# =============================================================================
# SECTION 5
# =============================================================================
heading("5. The Machine Learning Models Used", level=1)
para(
    "“Machine Learning” here means: a program that looks at a large table of past examples "
    "(e.g. “this team had this form, this rating, played this opponent, and the result was...”) "
    "and learns a general pattern it can apply to a brand-new, never-seen match or asset."
)

heading("Sports models", level=2)
bullet("ELO rating system -- a scoring method (originally from chess) that rates every team's strength and updates after each result.")
bullet("A trained ensemble ML model (scikit-learn based) blended with the ELO rating, head-to-head history, and recent streak/form.")
bullet("Separate sub-models for: match result (1X2), both-teams-to-score, total goals/runs over-under, corners.")

heading("Crypto & Stocks models", level=2)
bullet("A ranking model that scores each asset by predicted 6-month forward return.")
bullet("Inputs include price momentum, volatility, market capitalization, and (for stocks) fundamentals like P/E ratio.")

heading("Dubai Real Estate model", level=2)
bullet("A compound-growth formula: future value = current value × (1 + rate)^years -- the same math banks use for compound interest, applied to property value.")
bullet("The “rate” itself is adjusted by an infrastructure multiplier (metro proximity, development phase, commercial density) and a time-decay factor for holdings over 10 years.")

heading("Metals & Stones model", level=2)
bullet("Each metal/gemstone has a realistic base annual appreciation rate drawn from historical market behavior.")
bullet("A market-cycle multiplier (1.0x / 1.2x / 1.4x) is applied based on how volatile that specific metal has historically been -- higher volatility metals get a higher multiplier, reflecting higher growth potential alongside higher risk.")

para(
    "Important honesty note: predictions on every page carry a visible disclaimer -- these are "
    "estimates based on historical trends, not guarantees. Financial markets and real estate "
    "are influenced by many unpredictable real-world events.",
    italic=True
)

doc.add_page_break()

# =============================================================================
# SECTION 6 -- AWS
# =============================================================================
heading("6. AWS -- Where the Website Lives and How It's Deployed", level=1)

heading("6.1 The server itself", level=2)
para(
    "The live website runs on a single Amazon Web Services (AWS) EC2 instance -- essentially "
    "a rented virtual computer that stays switched on 24/7. It runs Ubuntu Linux."
)
add_table(
    headers=["Detail", "Value"],
    rows=[
        ("Cloud provider", "Amazon Web Services (AWS)"),
        ("Service", "EC2 (Elastic Compute Cloud) -- a virtual server"),
        ("Operating system", "Ubuntu Linux"),
        ("Web server software", "gunicorn (runs the Python/Flask app) behind Cloudflare"),
        ("Domain / CDN / HTTPS", "Cloudflare sits in front of the server"),
        ("Process manager", "systemd (Linux's built-in service manager) keeps the app, and all automation jobs, running"),
    ]
)

heading("6.2 Deploying a change (how code goes from your PC to the live site)", level=2)
para(
    "Every single fix or feature built for this project -- from the metals calculator to the "
    "mobile navigation redesign -- reached the live site through one script: deploy/sync.sh."
)
add_image("05_deploy_flow.png", caption="Figure 4 -- What happens when 'bash deploy/sync.sh' is run.")
para("In plain terms, deploying means:", bold=True)
numbered("Compress the webapp/ and scripts/ folders on your PC into a single package (excluding logs, caches, and secrets).")
numbered("Securely copy that package to the EC2 server over SSH (a secure remote-login protocol), using a private key file as the password.")
numbered("On the server, unpack the package into place, overwriting the old files.")
numbered("Restart the degen-predictions systemd service, which reloads the Python app with the new code.")
numbered("The site is now live with the change -- normally takes under 30 seconds start to finish.")

heading("6.3 Redeployment / continuous updates", level=2)
para(
    "“Redeployment” is just running deploy/sync.sh again after another change -- there is no "
    "separate process. Every fix documented in Section 9 of this document was shipped this way, "
    "often several times in a row while iterating on a fix."
)

heading("6.4 Keeping the server itself healthy", level=2)
para(
    "Two additional pieces of AWS-side automation keep the server itself reliable, independent "
    "of any code changes:"
)
bullet("A daily scheduled reboot (systemd timer, 4:00 AM UTC) clears any accumulated memory/network issues before the day's automation cycle runs.")
bullet("AWS's own health checks (System Status and Instance Status) can be queried any time via the AWS CLI to confirm the server's hardware and OS-level networking are both healthy.")

doc.add_page_break()

# =============================================================================
# SECTION 7 -- AUTOMATION
# =============================================================================
heading("7. Automation -- What Runs Automatically, Where, and When", level=1)
para(
    "A firm rule for this project: all data fetching and model training happens on the "
    "developer's own PC -- never on the EC2 server. The EC2 server's only job is to serve "
    "already-finished results to visitors, plus a couple of lightweight, always-on tasks "
    "(polling live matches, and a periodic MLB/NPB data refresh) that don't involve training."
)
add_image("04_automation.png", caption="Figure 5 -- Full automation map: local PC vs. EC2 server.")

heading("7.1 On your PC (Windows Task Scheduler)", level=2)
add_table(
    headers=["Job", "Schedule", "What it does"],
    rows=[
        ("DegenPredictions-DailyTraining", "Daily, 10:30 AM IST (= 5:00 AM UTC)",
         "Runs automation/scheduler.py: fetches fresh data, retrains models, "
         "generates predictions, and checks accuracy for all 5 categories, "
         "then pushes the results to EC2 via deploy/sync.sh."),
    ]
)

heading("7.2 On the EC2 server (systemd timers)", level=2)
add_table(
    headers=["Timer", "Schedule", "What it does"],
    rows=[
        ("degen-live-tracker.timer", "Every 5 minutes",
         "Single poll pass: checks for any football/baseball matches currently in progress and logs their live state."),
        ("degen-refresh-data.timer", "Every 3 hours",
         "Pre-existing job (built before this automation layer) that refreshes MLB/NPB fixtures and rebuilds ELO ratings -- lightweight, no model training."),
        ("degen-reboot.timer", "Daily, 4:00 AM UTC",
         "Reboots the EC2 instance for a clean slate, one hour before the daily training cycle finishes and pushes new results."),
    ]
)

para(
    "Why 5:00 AM UTC for training and 4:00 AM UTC for the reboot? The reboot runs first, so "
    "the server is fresh and stable before the day's newly trained predictions are deployed to "
    "it an hour later."
)

doc.add_page_break()

# =============================================================================
# SECTION 8 -- INCIDENT
# =============================================================================
heading("8. Incident History -- What Went Wrong and How It Was Fixed", level=1)
heading("8.1 Site outage (EC2 instance impaired)", level=2)
para(
    "On one occasion the live site returned a Cloudflare 522 error (“could not connect to the "
    "origin server”). Here's exactly how it was diagnosed and resolved."
)
add_image("06_incident.png", caption="Figure 6 -- Diagnosis and fix for the EC2 outage.")
para("Step-by-step diagnosis:", bold=True)
numbered("Confirmed the site itself was down (HTTP request returned Cloudflare error 522).")
numbered("Tried to SSH into the server directly -- that also timed out, which ruled out “just a web-server crash” and pointed to a deeper networking problem.")
numbered(
    "Used the AWS Command Line Interface (a text-based tool for controlling AWS resources) to "
    "check the instance's real status. AWS reports two separate health checks: 'System Status' "
    "(the physical hardware) and 'Instance Status' (the instance's own operating system). "
    "System Status was healthy, but Instance Status was 'impaired' -- meaning the OS-level "
    "network stack inside the virtual machine had hung, even though the underlying hardware was fine."
)
numbered("Fixed it with a single command: aws ec2 reboot-instances -- a soft reboot that keeps the same disk and data, just restarts the operating system.")
numbered("Verified recovery: the website returned HTTP 200 (success), SSH access was restored, and the app service was confirmed 'active' within about two minutes.")
para(
    "Prevention put in place: the daily 4:00 AM UTC reboot (Section 7.2) exists specifically so "
    "this class of hung-network issue gets cleared automatically before it can cause an outage.",
    bold=True
)

doc.add_page_break()

# =============================================================================
# SECTION 9 -- Mobile & design fixes log
# =============================================================================
heading("9. Mobile & Design Fixes -- A Running Log", level=1)
para(
    "The site went through several rounds of mobile-specific bug fixes. Each one is recorded "
    "here with the plain-English symptom and the actual root cause, since several turned out "
    "to share the same underlying pattern."
)

add_table(
    headers=["Symptom (what the user saw)", "Root Cause", "Fix"],
    rows=[
        ("Touch/taps not registering on iPhone/Android",
         "An invisible notification container ('toast stack') had all four sides pinned on mobile, stretching it to cover the entire screen and silently absorbing every tap.",
         "Added pointer-events: none to the empty container, pointer-events: auto to actual visible notifications."),
        ("Mobile nav bar showed as an odd 2x3 grid of boxes",
         "Each nav link had 'flex: 1' forcing equal-width boxes, which wrapped awkwardly for longer labels like 'Dubai Real Estate'.",
         "Redesigned as a single-row horizontally scrollable pill nav with no text wrapping."),
        ("'Get Predictions' button oversized and overflowing off-screen",
         "The button reused a class forced to 'width: 100%' by the global mobile stylesheet, which conflicted with 'flex-shrink: 0' inside a row it shared with the match info.",
         "Stacked the match info and button vertically on mobile instead of side-by-side."),
        ("Dubai calculator: results panel cut off; holding-period labels jumbled together",
         "The two-column layout had no mobile breakpoint, and 8 label spans lacked 'white-space: nowrap', so words split mid-phrase.",
         "Added a mobile breakpoint to stack to one column, and made labels wrap as whole units."),
        ("Donation crypto address shown as one character per line",
         "The 'Copy' button reused a class forced to 'width: 100%', which squeezed the address text down to almost no width; combined with 'word-break: break-all' this broke after every single character.",
         "Gave the Copy button a fixed, content-sized width so the address field keeps its fair share of space."),
    ]
)

para(
    "Common thread: several of these bugs came from the same source -- a class-wide mobile rule "
    "(like 'width: 100%' on all buttons) that made sense for standalone buttons, but broke "
    "layouts where that same button class was reused inside a shared row alongside other content. "
    "The general fix pattern was always the same: either stack the row vertically on mobile, or "
    "give the smaller element a fixed, content-sized width so it stops competing for space."
)

doc.add_page_break()

# =============================================================================
# SECTION 10 -- LIVE MARKET DATA
# =============================================================================
heading("10. Live Market Data -- Real Prices From TradingView & Yahoo Finance", level=1)
para(
    "Early versions of the Metals calculator used hardcoded prices that were only accurate on "
    "the day they were typed in -- by the time real gold was trading near $4,000/oz, the "
    "calculator was still showing $2,050. This section covers how that was fixed with genuinely "
    "live data, and exactly which metals still can't be (with the real reason why, not a guess)."
)
add_image("07_live_market_data.png", caption="Figure 7 -- Where each metal's price actually comes from.")

heading("10.1 Two real data sources, verified directly", level=2)
para(
    "Rather than trust an API's documentation, every symbol used here was tested by directly "
    "querying the provider and confirming it returns real, current, non-empty price data:"
)
bullet(
    "TradingView's public scanner API (scanner.tradingview.com) -- the same backend the page's "
    "own live chart widget reads from. Used for Gold, Silver, Platinum, Palladium, Copper, and Steel."
)
bullet(
    "Yahoo Finance's chart API (query1.finance.yahoo.com) -- a fallback for Aluminum, which "
    "TradingView's free-tier scanner won't resolve without a paid account."
)
para(
    "Zinc, Nickel, and Lithium have no free live source anywhere -- this was tested directly "
    "(including trying 4 different TradingView \"scanner regions\": global, cfd, america, forex) "
    "before concluding this, not assumed. These three stay as periodically hand-updated "
    "benchmarks, clearly labelled as such on the Market Insights page.",
    italic=True
)

heading("10.2 How it reaches the website", level=2)
numbered("The daily automation job (Section 7) fetches fresh prices from both sources and writes them to data/metals/current_prices.json.")
numbered("metals_predict.py reads that file at request time and overlays live prices onto its static defaults -- if the file is missing or a specific metal failed to fetch that day, the static value is used instead, so the calculator never breaks.")
numbered("The result: 7 of the calculator's metals refresh automatically every day with zero manual work.")

doc.add_page_break()

# =============================================================================
# SECTION 11 -- CRYPTO & STOCKS LIVE CHARTS
# =============================================================================
heading("11. Crypto & Stocks -- Live Charts and the Click-to-Sync Feature", level=1)
para(
    "The Crypto, US Stocks, and India Stocks ranking pages each got the same live-chart "
    "treatment as Metals -- a candlestick chart above the rankings table that switches to "
    "whichever asset you click on."
)
add_image("08_crypto_stocks_widget.png", caption="Figure 8 -- How clicking a row updates the chart.")

heading("11.1 Building a verified ticker map -- not a guess", level=2)
para(
    "The site's crypto and stock data uses tickers from CoinGecko (e.g. \"ondo-finance\") and "
    "standard stock symbols (e.g. \"AAPL\", \"RELIANCE.NS\"). None of these are guaranteed to "
    "match TradingView's own symbol format, so every single one was checked programmatically "
    "against TradingView's live API before being added to the map used on the site:"
)
add_table(
    headers=["Category", "Total Tickers", "Resolved on TradingView", "Why the gap (if any)"],
    rows=[
        ("Crypto", "60", "50 (83%)", "10 are tokenized institutional fund/RWA products (e.g. BlackRock's BUIDL, "
                                       "Hashnote's USYC) with no real exchange listing to source a price from."),
        ("US + India Stocks", "553", "553 (100%)", "No gaps -- two tricky edge cases needed manual "
                                                     "correction (CBOE trades on an exchange literally "
                                                     "named CBOE; India's Bajaj Auto uses an underscore, "
                                                     "not a dash, on the NSE)."),
    ]
)
para(
    "This map lives in webapp/tv_symbols.py, with the verification method documented directly "
    "in the file's own comments."
)

heading("11.2 A real bug worth recording: quoting inside HTML attributes", level=2)
para(
    "The first version of this feature appeared broken -- clicking any row always reloaded the "
    "same first asset, no matter what was clicked. The cause: the ticker was inserted into the "
    "row's onclick attribute using Flask's tojson filter, e.g. onclick=\"loadAssetWidget({{ ticker"
    "|tojson }})\". Flask's tojson deliberately skips extra escaping because it's designed for use "
    "inside <script> blocks -- but here it was used inside an HTML attribute instead, so the raw "
    "quote characters in the output (\"zcash\") collided with the attribute's own quotes and cut "
    "it short: onclick=\"loadAssetWidget(\". Every row's click handler was silently truncated the "
    "same way.",
)
para(
    "Fix: moved the ticker into its own data-tv-ticker=\"...\" attribute (plain HTML escaping, not "
    "tojson) and read it back with this.dataset.tvTicker inside the click handler instead of "
    "embedding it directly in the onclick call.",
    bold=True
)

doc.add_page_break()

# =============================================================================
# SECTION 12 -- SEO, AEO & GEO
# =============================================================================
heading("12. SEO, AEO & GEO -- How the Site Gets Found", level=1)
para(
    "Three related but distinct goals: ranking in traditional search (SEO), getting quoted in "
    "featured snippets and voice assistant answers (AEO -- Answer Engine Optimization), and "
    "being cited directly by AI chat tools like ChatGPT or Perplexity (GEO -- Generative Engine "
    "Optimization). The site was audited and updated across all three."
)
add_image("09_seo_aeo_geo.png", caption="Figure 9 -- One set of content, three different audiences.")

heading("12.1 SEO fixes", level=2)
bullet("The sitemap was missing /dubai, /dubai/guide, /metals, and /metals/insights entirely -- these are among the newest, most-featured pages on the site, and search engines had no crawl signal pointing to them at all.")
bullet("Deleted a dead, unused duplicate sitemap generator left over from an earlier refactor.")
bullet("Added Service, BreadcrumbList, and ItemList structured data (JSON-LD, a standard search engines read directly) to every major calculator and rankings page.")
bullet("Fixed a footer disclaimer and several meta descriptions that still didn't mention Metals at all, months after that category launched.")

heading("12.2 AEO -- visible FAQ sections with FAQPage schema", level=2)
para(
    "The Homepage, Dubai, and Metals pages each got a short, direct Q&A section (e.g. \"How is "
    "Dubai real estate ROI calculated?\") marked up with FAQPage schema. This structure is what "
    "Google pulls directly into featured snippets, and what voice assistants read aloud verbatim "
    "-- the same content serves both a human skimming the page and a search engine's snippet "
    "extractor."
)

heading("12.3 GEO -- llms.txt", level=2)
para(
    "Created /llms.txt, following the emerging llmstxt.org convention -- a clean, structured, "
    "plain-text map of every page and what it does, aimed specifically at AI crawlers (ChatGPT, "
    "Perplexity, Claude, Gemini) rather than traditional search bots. The goal: when someone asks "
    "an AI assistant \"what's a good Dubai ROI calculator,\" the assistant can read this file "
    "directly instead of having to guess from a rendered page."
)

doc.add_page_break()

# =============================================================================
# SECTION 13 -- VERSION CONTROL
# =============================================================================
heading("13. Version Control -- GitHub, Licensing, and Repository History", level=1)
para(
    "This project began as a clone of a public GitHub repository (a different developer's "
    "single-sport football prediction project). Over the course of development, every piece of "
    "that original code, data, and trained model was replaced -- the repository was formally "
    "cleaned up to reflect that reality."
)

heading("13.1 What was actually inherited from the original clone", level=2)
para(
    "A direct comparison against the original project's first commit confirmed: none of its "
    "data (2016-2021 season CSVs), models (a single XGBoost + single decision tree file), or "
    "code (its static-HTML-generation scripts) survived. The only genuine remnant was the git "
    "commit history itself and a remote URL pointing back to the original author's GitHub."
)
bullet("All 64 of the original author's files (LICENSE, shell-script automation, the static web/ template, notebooks, old models) were committed as deletions.")
bullet("All of the actual, currently-live project -- the webapp/ Flask app, training scripts, automation -- was committed for the first time in the same cleanup (it had been sitting as uncommitted local files the entire time before this).")
bullet("The origin remote pointing to the original author's repository was removed.")

heading("13.2 New private repository", level=2)
para(
    "The project was pushed to a brand-new, private GitHub repository under the developer's own "
    "account, authenticated via GitHub's device-code browser sign-in flow (no token pasted into "
    "any chat or file). A proprietary \"All Rights Reserved\" LICENSE was added -- deliberately "
    "not an open-source license like MIT, since a private repository paired with a permissive "
    "license would be contradictory."
)
bullet("Repository visibility confirmed as PRIVATE via the GitHub API -- no one can access it without being explicitly invited as a collaborator.")
bullet("Every commit is authored under the developer's own name and email, with a \"Co-Authored-By\" trailer crediting the AI assistant -- the standard GitHub convention for AI-assisted commits.")

doc.add_page_break()

# =============================================================================
# SECTION 14 -- SECOND BUG-FIX WAVE
# =============================================================================
heading("14. Second Bug-Fix Wave -- Sticky Header, Widget Overlap, and More", level=1)
para(
    "A second round of bugs surfaced after the live-chart and SEO work shipped. Several of these "
    "were subtle CSS interactions -- the kind where the fix looks like a one-line change, but "
    "finding the actual cause took real investigation."
)

add_table(
    headers=["Symptom", "Root Cause", "Fix"],
    rows=[
        ("Sticky header appeared to \"jump\" while scrolling on some devices",
         "position: sticky combined with backdrop-filter: blur() is a known rendering bug on Safari -- the browser mishandles compositing for blurred sticky elements.",
         "Forced the header onto its own GPU-composited layer with transform: translateZ(0) and will-change: transform."),
        ("Header still moved/scrolled away after that first fix",
         "A separate, larger issue: html, body { overflow-x: hidden } (added earlier to stop stray horizontal scroll) makes body -- instead of the actual viewport -- the \"nearest scrolling ancestor\" that sticky positioning resolves against, breaking it outright.",
         "Moved overflow-x: hidden to <html> only. Still blocks the same horizontal-scroll bug, without breaking position: sticky anywhere on the page."),
        ("TradingView chart widgets appeared to overlap the search box, table, or disclaimer popup",
         "TradingView's embed script builds its chart iframe as position: absolute, expecting the wrapping .tradingview-widget-container to have position: relative -- which the site's CSS never defined. The iframe was positioning itself against the wrong ancestor.",
         "Added position: relative (and width: 100%) to .tradingview-widget-container as a sitewide rule, fixing every embedded widget at once."),
        ("Dubai calculator: left input panel had a large empty gap at the bottom",
         "The two-column layout is a CSS grid with the default align-items: stretch, which force-stretches the shorter input panel to match the much taller results panel.",
         "Set align-items: start on the grid so each panel sizes to its own content -- then added a genuinely useful 7-area price/infrastructure comparison table to the input panel so the space holds real content, not just a shorter box."),
        ("Metals category card looked almost identical in color to the Dubai card",
         "Both used a similar warm gold/tan accent color.",
         "Gave Metals a distinct cool steel-silver accent color instead."),
        ("MLB/NPB league badge text (\"MLB\", \"NPB\") overflowed its circular badge on mobile",
         "The badge circle shrinks from 120px to 70px on mobile, but the text inside kept the desktop font-size.",
         "Added a mobile-specific smaller font-size and removed letter-spacing for the badge text."),
        ("Dropdown menus (Dubai, Metals, donation modal) looked like plain grey system popups, inconsistent with the rest of the site",
         "Native <select> elements only let CSS style the closed box -- the browser renders the opened option list itself and ignores page styling entirely.",
         "Replaced every native <select> sitewide with the same custom-built dropdown component (already used on the fixtures page), extracted into a shared static/custom-select.js file so all pages stay visually consistent."),
        ("FAQ content was duplicated across the Homepage, Dubai, and Metals pages, and missing entirely for Crypto/Stocks",
         "Each page had grown its own local FAQ section independently over time.",
         "Consolidated everything into one dedicated /faq page (added via the footer, shown on every page), removed the duplicated sections from the individual pages, and added the two categories that had no FAQ coverage at all."),
    ]
)
para(
    "One more cross-checking note: several category descriptions (footer, homepage cards, structured data, llms.txt) "
    "said \"football and baseball\" and had quietly fallen out of sync after cricket predictions were added -- "
    "a reminder that adding a feature in one place doesn't automatically update every other place that describes it. "
    "All instances were found with a sitewide search and corrected together, not fixed one at a time as each was spotted.",
    italic=True
)

doc.add_page_break()

# =============================================================================
# SECTION 15 -- GLOSSARY
# =============================================================================
heading("15. Glossary -- Plain-English Definitions", level=1)
glossary = [
    ("AI (Artificial Intelligence)", "Broad term for software that performs tasks normally requiring human judgment. Machine Learning is one specific technique used to build AI."),
    ("ML (Machine Learning)", "A program that learns patterns from historical examples (data) instead of being explicitly programmed with fixed rules."),
    ("ELO rating", "A numeric skill rating (originally created for chess) that goes up when a team/player wins and down when they lose, weighted by the opponent's own rating."),
    ("API", "A way for two pieces of software to talk to each other over the internet -- e.g. fetching live crypto prices from CoinGecko's API."),
    ("Flask", "The Python framework used to build this website's server-side code (app.py and related files)."),
    ("gunicorn", "Production-grade software that runs a Flask app reliably and handles many visitors at once (the plain 'python app.py' command is only meant for testing)."),
    ("EC2", "Amazon's \"Elastic Compute Cloud\" -- a rented virtual server that runs 24/7 in Amazon's data centers."),
    ("AWS CLI", "A command-line tool for controlling AWS resources (like checking or rebooting an EC2 instance) without using the web-based AWS Console."),
    ("SSH", "A secure way to remotely log in to and control another computer over the internet, authenticated with a private key file instead of a password."),
    ("systemd", "Linux's built-in system for managing background services and scheduled jobs (called \"timers\")."),
    ("systemd timer", "A scheduled task on Linux, similar to Windows Task Scheduler -- e.g. \"run this script every day at 4 AM.\""),
    ("Cloudflare", "A service that sits in front of a website to handle its domain name, provide HTTPS security, and protect against attacks before traffic reaches the real server."),
    ("CSV / JSON", "Two common plain-text file formats used to store data -- CSV is spreadsheet-like rows and columns; JSON is a more flexible, nested format."),
    ("Compound growth", "Growth where each year's gain is calculated on top of the previous year's already-grown value -- the same principle as compound interest in banking."),
    ("Cache / TTL", "A temporary copy of data kept in memory to avoid re-reading files on every single request; TTL (\"Time To Live\") is how long that copy stays valid before it's refreshed."),
    ("Volatility", "A measure of how much an asset's price swings up and down -- higher volatility means bigger, less predictable price movements."),
    ("ROI (Return on Investment)", "The total gain from an investment, shown as a percentage of the amount originally invested."),
    ("SEO (Search Engine Optimization)", "Practices that help a website rank higher in traditional search results (Google, Bing)."),
    ("AEO (Answer Engine Optimization)", "Structuring content so it can be lifted directly into a featured snippet or read aloud by a voice assistant, rather than requiring a click-through."),
    ("GEO (Generative Engine Optimization)", "Structuring content so AI chat tools (ChatGPT, Perplexity, etc.) can find, understand, and cite it directly in their answers."),
    ("JSON-LD / Schema.org", "A standardized, machine-readable format embedded in a webpage's code that explicitly tells search engines and AI crawlers what the page is about (e.g. \"this is a Service\" or \"this is a list of Questions and Answers\")."),
    ("Sitemap", "A single file (sitemap.xml) listing every page on a site, submitted to search engines so they know what exists and how often to re-check it."),
    ("llms.txt", "A plain-text file at a site's root, following an emerging community standard, that gives AI crawlers a clean summary of the site's pages -- the AI equivalent of a sitemap."),
    ("TradingView", "A financial charting platform. Its free embeddable widgets were used to show live commodity/crypto/stock prices directly on the site, and its public API was used to verify which tickers actually have real listings."),
    ("CFD (Contract for Difference)", "A financial instrument that tracks an asset's price without the trader owning the underlying asset -- several of TradingView's commodity price feeds are sourced from CFD providers."),
    ("position: sticky", "A CSS rule that keeps an element (like a navigation bar) pinned to the top of the screen while the rest of the page scrolls beneath it -- it has several well-known browser-specific bugs when combined with other effects like blur."),
    ("GPU compositing / transform: translateZ(0)", "A technique that forces the browser to render an element on its own hardware-accelerated layer, which incidentally fixes certain rendering bugs (like a sticky header appearing to jump) even though that isn't its primary purpose."),
    ("Git / GitHub", "Git is the version-control system that tracks every change made to the project's code over time. GitHub is the website that hosts a copy of that history online, privately or publicly."),
    ("Repository (\"repo\")", "The full folder of project code plus its entire change history, as tracked by Git."),
    ("Commit", "A single saved snapshot of changes in Git, with an author and a message describing what changed."),
]
for term, definition in glossary:
    p = doc.add_paragraph()
    r1 = p.add_run(term + " -- ")
    r1.font.bold = True
    r1.font.color.rgb = GOLD
    r2 = p.add_run(definition)
    p.paragraph_format.space_after = Pt(6)

doc.add_paragraph()
closing = doc.add_paragraph()
closing.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = closing.add_run(
    "End of document. This file covers the project end-to-end as of the date it was generated -- "
    "future changes should be added as new entries in Section 9's running log."
)
r.italic = True
r.font.size = Pt(9.5)
r.font.color.rgb = DARKGREY

doc.save(OUT_PATH)
print("Saved:", OUT_PATH)
