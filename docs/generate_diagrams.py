"""
Generates all diagram PNGs used in the Degen Predictions documentation.
Run once locally: python generate_diagrams.py
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.path import Path
import os

OUT = os.path.join(os.path.dirname(__file__), "diagrams")
os.makedirs(OUT, exist_ok=True)

NAVY = "#0a0e27"
BOX_BLUE = "#1a2550"
BORDER = "#00d4ff"
GOLD = "#ffd700"
GREEN = "#00c853"
RED = "#ff4466"
TEXT = "#ffffff"
GREY = "#a0b0d0"


def box(ax, x, y, w, h, text, fc=BOX_BLUE, ec=BORDER, fontsize=11, fontweight="bold", textcolor=TEXT):
    b = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                        linewidth=2, edgecolor=ec, facecolor=fc, zorder=2)
    ax.add_patch(b)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
             fontsize=fontsize, fontweight=fontweight, color=textcolor, zorder=3, wrap=True)
    return (x, y, w, h)


def arrow(ax, start, end, color=BORDER, style="->", lw=2.2, connectionstyle="arc3,rad=0.0"):
    a = FancyArrowPatch(start, end, arrowstyle=style, mutation_scale=18,
                         color=color, linewidth=lw, zorder=1, connectionstyle=connectionstyle)
    ax.add_patch(a)


def new_fig(w=13, h=8):
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor(NAVY)
    ax.set_facecolor(NAVY)
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.axis("off")
    return fig, ax


def save(fig, name):
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=170, facecolor=NAVY, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    print("saved", path)


# ---------------------------------------------------------------------------
# 1. High-level system architecture
# ---------------------------------------------------------------------------
fig, ax = new_fig(13, 7.5)
ax.text(6.5, 7.1, "Degen Predictions -- System Architecture", ha="center", fontsize=17,
        fontweight="bold", color=TEXT)

box(ax, 0.4, 5.2, 2.6, 1.2, "Visitor's Phone\nor Laptop\n(Browser)", fc="#152040")
box(ax, 3.6, 5.2, 2.6, 1.2, "Cloudflare\n(DNS + HTTPS +\nSecurity Filter)", fc="#152040")
box(ax, 6.8, 5.2, 2.8, 1.2, "AWS EC2 Server\n(Ubuntu, one machine,\nalways on)", fc=BOX_BLUE, ec=GOLD)
box(ax, 10.2, 5.2, 2.4, 1.2, "gunicorn\n(runs the Flask\nwebsite code)", fc=BOX_BLUE)

arrow(ax, (3.0, 5.8), (3.6, 5.8))
arrow(ax, (6.2, 5.8), (6.8, 5.8))
arrow(ax, (9.6, 5.8), (10.2, 5.8))

box(ax, 6.8, 3.2, 2.8, 1.2, "Flask App\n(app.py)\nthe website's brain", fc=BOX_BLUE, ec=GOLD)
arrow(ax, (11.4, 5.2), (8.2, 4.4), connectionstyle="arc3,rad=-0.3")

box(ax, 0.6, 1.0, 3.0, 1.4, "Data files\n(CSV / JSON)\nfixtures, prices,\npredictions", fc="#152040", ec=GREEN)
box(ax, 4.2, 1.0, 3.0, 1.4, "Trained ML Models\n(saved model files)\ngold, crypto, football...", fc="#152040", ec=GREEN)
box(ax, 7.8, 1.0, 3.0, 1.4, "HTML Templates\n+ CSS/JS\n(what you see)", fc="#152040", ec=GREEN)

arrow(ax, (2.1, 2.4), (7.3, 3.2), connectionstyle="arc3,rad=0.25")
arrow(ax, (5.7, 2.4), (7.7, 3.2), connectionstyle="arc3,rad=0.15")
arrow(ax, (9.3, 2.4), (8.6, 3.2), connectionstyle="arc3,rad=-0.1")

ax.text(6.5, 0.3, "Everything below the Flask box lives on the EC2 server's disk and is\n"
                  "read fresh on every visit (or from a short-lived cache) -- nothing is fetched live from your PC.",
        ha="center", fontsize=9.5, color=GREY, style="italic")

save(fig, "01_architecture.png")

# ---------------------------------------------------------------------------
# 2. The 5 prediction categories
# ---------------------------------------------------------------------------
fig, ax = new_fig(13, 6)
ax.text(6.5, 5.6, "What Degen Predictions Covers", ha="center", fontsize=17, fontweight="bold", color=TEXT)
box(ax, 5.2, 3.7, 2.6, 1.1, "DEGEN\nPREDICTIONS", fc=BOX_BLUE, ec=GOLD, fontsize=13)

cats = [
    ("Sports\nFootball + Baseball\nmatch predictions", 0.4, 1.3, "#2e7d32"),
    ("Crypto\nAI-ranked coins by\npredicted return", 2.9, 1.3, "#f7931a"),
    ("Stocks\nUS + India rankings\nby predicted return", 5.4, 1.3, "#1565c0"),
    ("Dubai Real Estate\nProperty ROI +\ninfrastructure growth", 7.7, 1.3, "#d4a574"),
    ("Metals & Stones\nGold, silver, gemstones\nprice forecasts", 10.3, 1.3, "#c9a961"),
]
for text, x, y, color in cats:
    box(ax, x, y, 2.3, 1.6, text, fc="#152040", ec=color, fontsize=9.5)
    cx = x + 1.15
    arrow(ax, (6.5, 3.7), (cx, y + 1.6), connectionstyle="arc3,rad=0.0")

save(fig, "02_categories.png")

# ---------------------------------------------------------------------------
# 3. Per-category prediction pipeline (fetch -> train -> predict -> serve)
# ---------------------------------------------------------------------------
fig, ax = new_fig(13, 4.5)
ax.text(6.5, 4.1, "How One Prediction Gets Made (every category follows this)", ha="center",
        fontsize=15, fontweight="bold", color=TEXT)

steps = [
    ("1. Fetch Data\nGet the latest prices /\nfixtures / market info", GREEN),
    ("2. Train Model\nTeach the ML model\nusing history + new data", GOLD),
    ("3. Predict\nModel produces a\nforecast / ranking", "#00d4ff"),
    ("4. Store Result\nSave as a data file\n(CSV / JSON)", "#9933ff"),
    ("5. Serve to Website\nFlask reads the file and\nshows it to visitors", RED),
]
w = 2.2
gap = 0.35
start_x = 0.5
for i, (text, color) in enumerate(steps):
    x = start_x + i * (w + gap)
    box(ax, x, 1.6, w, 1.6, text, fc="#152040", ec=color, fontsize=9)
    if i < len(steps) - 1:
        arrow(ax, (x + w, 2.4), (x + w + gap, 2.4))

save(fig, "03_prediction_pipeline.png")

# ---------------------------------------------------------------------------
# 4. Automation & scheduling map
# ---------------------------------------------------------------------------
fig, ax = new_fig(13, 8.5)
ax.text(6.5, 8.1, "Automation -- What Runs, Where, and When", ha="center", fontsize=16,
        fontweight="bold", color=TEXT)

box(ax, 0.4, 6.0, 5.6, 1.6, "YOUR PC (Windows)\nWindows Task Scheduler\nDaily @ 10:30 AM IST = 5:00 AM UTC",
    fc="#152040", ec=GOLD, fontsize=10.5)
box(ax, 1.0, 4.2, 4.4, 1.4, "automation/scheduler.py\nFetch -> Retrain -> Predict -> Track\n(ALL training happens HERE, never on EC2)",
    fc=BOX_BLUE, ec=GREEN, fontsize=9.5)
arrow(ax, (3.2, 6.0), (3.2, 5.6))

box(ax, 1.0, 2.4, 4.4, 1.3, "deploy/sync.sh\nPackages data + models,\nuploads to EC2 over SSH", fc=BOX_BLUE, ec="#00d4ff", fontsize=9.5)
arrow(ax, (3.2, 4.2), (3.2, 3.7))

arrow(ax, (5.4, 3.05), (7.0, 3.05))

box(ax, 7.0, 6.0, 5.6, 1.6, "AWS EC2 SERVER (Ubuntu, systemd timers)\nAlways on -- only SERVES results, never trains",
    fc="#152040", ec=RED, fontsize=10.5)

box(ax, 7.3, 4.2, 2.5, 1.4, "degen-predictions\n(gunicorn + Flask)\nrestarted after each\ndeploy to clear cache", fc=BOX_BLUE, ec=GREEN, fontsize=8.7)
box(ax, 10.1, 4.2, 2.5, 1.4, "degen-live-tracker\nEvery 5 minutes\npolls for live match\nupdates", fc=BOX_BLUE, ec="#00d4ff", fontsize=8.7)
arrow(ax, (8.55, 6.0), (8.55, 5.6))
arrow(ax, (11.35, 6.0), (11.35, 5.6))

box(ax, 7.3, 2.4, 2.5, 1.3, "degen-refresh-data\nEvery 3 hours\nMLB/NPB fixture +\nElo refresh", fc=BOX_BLUE, ec=GOLD, fontsize=8.7)
box(ax, 10.1, 2.4, 2.5, 1.3, "degen-reboot\nDaily @ 4:00 AM UTC\nclean instance restart\n(1 hr before training)", fc=BOX_BLUE, ec=RED, fontsize=8.7)
arrow(ax, (8.55, 4.2), (8.55, 3.7))
arrow(ax, (11.35, 4.2), (11.35, 3.7))

ax.text(9.75, 1.4, "All EC2 jobs are 'systemd timers' -- Linux's built-in alarm clock feature.\n"
                   "None of them fetch data or train models; that rule lives only on your PC.",
        ha="center", fontsize=9.5, color=GREY, style="italic")

save(fig, "04_automation.png")

# ---------------------------------------------------------------------------
# 5. Deploy / redeploy flow
# ---------------------------------------------------------------------------
fig, ax = new_fig(13, 6.5)
ax.text(6.5, 6.1, "Deploying a Code Change (deploy/sync.sh)", ha="center", fontsize=16,
        fontweight="bold", color=TEXT)

box(ax, 0.3, 4.3, 2.4, 1.3, "1. Edit code\non your PC\n(webapp/, scripts/)", fc="#152040", ec=GREEN, fontsize=9)
box(ax, 3.1, 4.3, 2.4, 1.3, "2. Pack\ntar --exclude caches,\nlogs, secrets", fc="#152040", ec=GOLD, fontsize=9)
box(ax, 5.9, 4.3, 2.4, 1.3, "3. Upload (scp)\nover SSH using the\nEC2 key-pair", fc="#152040", ec="#00d4ff", fontsize=9)
box(ax, 8.7, 4.3, 2.4, 1.3, "4. Extract on EC2\n(ssh) unzip into\nDegen_predictions/", fc="#152040", ec="#9933ff", fontsize=9)
box(ax, 8.7, 2.2, 2.4, 1.3, "5. Restart service\nsystemctl restart\ndegen-predictions", fc="#152040", ec=RED, fontsize=9)
box(ax, 5.9, 2.2, 2.4, 1.3, "6. Live!\nSite serves the\nnew version", fc=BOX_BLUE, ec=GOLD, fontsize=9)

arrow(ax, (2.7, 5.0), (3.1, 5.0))
arrow(ax, (5.5, 5.0), (5.9, 5.0))
arrow(ax, (8.3, 5.0), (8.7, 5.0))
arrow(ax, (9.9, 4.3), (9.9, 3.5))
arrow(ax, (8.7, 2.85), (8.3, 2.85))

ax.text(6.5, 1.0, "One command -- \"bash deploy/sync.sh\" -- runs all 6 steps automatically.\n"
                  "This is how every fix in this project (mobile layout, pricing models, theme, etc.) reached the live site.",
        ha="center", fontsize=9.5, color=GREY, style="italic")

save(fig, "05_deploy_flow.png")

# ---------------------------------------------------------------------------
# 6. EC2 outage incident (what happened, how it was fixed)
# ---------------------------------------------------------------------------
fig, ax = new_fig(13, 5.5)
ax.text(6.5, 5.1, "Incident: Site Went Down, How It Was Diagnosed & Fixed", ha="center",
        fontsize=15, fontweight="bold", color=TEXT)

box(ax, 0.3, 3.2, 2.6, 1.3, "Symptom\nSite returned\nCloudflare 522\n(cannot connect)", fc="#2a1220", ec=RED, fontsize=9)
box(ax, 3.2, 3.2, 2.6, 1.3, "Checked\nSSH also timed out --\nnot just the website", fc="#2a1220", ec=RED, fontsize=9)
box(ax, 6.1, 3.2, 2.9, 1.3, "Diagnosed (AWS CLI)\nInstance 'running' but\nInstanceStatus = impaired\n(network stack hung)", fc="#152040", ec=GOLD, fontsize=8.7)
box(ax, 9.3, 3.2, 3.0, 1.3, "Fix\naws ec2 reboot-instances\n(soft reboot, same disk,\nno data lost)", fc="#0d2b1a", ec=GREEN, fontsize=8.7)

arrow(ax, (2.9, 3.85), (3.2, 3.85))
arrow(ax, (5.8, 3.85), (6.1, 3.85))
arrow(ax, (9.0, 3.85), (9.3, 3.85))

box(ax, 3.6, 0.9, 5.6, 1.3, "Result: site + SSH + app service all back within ~2 minutes.\n"
                            "Prevention: daily scheduled reboot added at 4:00 AM UTC\n"
                            "so this class of hang gets cleared automatically going forward.",
    fc="#152040", ec="#00d4ff", fontsize=9)
arrow(ax, (10.8, 3.2), (7.5, 2.2), connectionstyle="arc3,rad=0.3")

save(fig, "06_incident.png")

print("All diagrams generated in", OUT)
