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
    "10. Glossary -- Plain-English Definitions of Every Technical Term Used",
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

heading("Metals & Stones", level=2)
para(
    "A similar calculator for precious metals (gold, silver, platinum, palladium), industrial "
    "metals (copper, aluminum, lithium, etc.), and gemstones (diamond, ruby, emerald, sapphire). "
    "Enter an investment amount and a holding period (6 months to 25 years); it returns a "
    "forecasted price and total return, adjusted by a market-cycle multiplier based on how "
    "volatile that metal historically is."
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
# SECTION 10 -- GLOSSARY
# =============================================================================
heading("10. Glossary -- Plain-English Definitions", level=1)
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
