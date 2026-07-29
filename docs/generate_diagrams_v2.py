"""
Additional diagrams for everything added since the original documentation:
live market data (TradingView + Yahoo Finance), the crypto/stocks widget
sync flow, and the SEO/AEO/GEO structure. Run once: python generate_diagrams_v2.py
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
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
PURPLE = "#9933ff"


def box(ax, x, y, w, h, text, fc=BOX_BLUE, ec=BORDER, fontsize=10.5, fontweight="bold", textcolor=TEXT):
    b = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.08",
                        linewidth=2, edgecolor=ec, facecolor=fc, zorder=2)
    ax.add_patch(b)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
             fontsize=fontsize, fontweight=fontweight, color=textcolor, zorder=3, wrap=True)


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
# 7. Live market data -- dual-source architecture (metals)
# ---------------------------------------------------------------------------
fig, ax = new_fig(13, 8.5)
ax.text(6.5, 8.1, "Live Market Data -- Metals Price Sourcing", ha="center", fontsize=16,
        fontweight="bold", color=TEXT)
ax.text(6.5, 7.6, "Zinc, Nickel, Lithium: no free live source exists anywhere (verified) -- stay static benchmarks.",
        ha="center", fontsize=9, color=GREY, style="italic")

box(ax, 0.4, 5.7, 5.6, 1.3, "TradingView Scanner API\n(scanner.tradingview.com)\nSame backend the page's own chart widget reads from",
    fc="#152040", ec=BORDER, fontsize=9.5)
box(ax, 7.0, 5.7, 5.6, 1.3, "Yahoo Finance Chart API\n(query1.finance.yahoo.com)\nFallback for symbols TradingView's free tier won't resolve",
    fc="#152040", ec=GOLD, fontsize=9.5)

box(ax, 0.4, 3.7, 5.6, 1.3, "Gold, Silver, Platinum,\nPalladium, Copper, Steel\n(6 metals -- exact match to the widget)",
    fc=BOX_BLUE, ec=GREEN, fontsize=9.5)
box(ax, 7.0, 3.7, 5.6, 1.3, "Aluminum\n(1 metal -- real live data,\njust a different venue than the widget)",
    fc=BOX_BLUE, ec=GOLD, fontsize=9.5)

arrow(ax, (3.2, 5.7), (3.2, 5.0))
arrow(ax, (9.8, 5.7), (9.8, 5.0))

box(ax, 3.7, 1.7, 5.6, 1.3, "data/metals/current_prices.json\nWritten daily by automation/scheduler.py\n(runs on local PC, 5:00 AM UTC)",
    fc="#152040", ec=PURPLE, fontsize=9.5)
arrow(ax, (3.2, 3.7), (5.5, 3.0), connectionstyle="arc3,rad=0.15")
arrow(ax, (9.8, 3.7), (8.2, 3.0), connectionstyle="arc3,rad=-0.15")

box(ax, 3.7, 0.3, 5.6, 1.1, "metals_predict.py overlays live prices onto\nstatic fallback data at request time", fc=BOX_BLUE, ec=GREEN, fontsize=9)
arrow(ax, (6.5, 1.7), (6.5, 1.4))

save(fig, "07_live_market_data.png")

# ---------------------------------------------------------------------------
# 8. Crypto/Stocks widget click-to-sync flow
# ---------------------------------------------------------------------------
fig, ax = new_fig(13, 6.5)
ax.text(6.5, 6.1, "Crypto & Stocks -- Click-to-Chart Sync", ha="center", fontsize=16,
        fontweight="bold", color=TEXT)

box(ax, 0.4, 4.0, 3.6, 1.3, "1. Page loads\nTop-ranked asset's\nchart shown by default", fc="#152040", ec=GREEN, fontsize=9.5)
box(ax, 4.6, 4.0, 3.6, 1.3, "2. User clicks\nany row in the\nrankings table", fc="#152040", ec=GOLD, fontsize=9.5)
box(ax, 8.8, 4.0, 3.8, 1.3, "3. Ticker looked up in\nverified symbol map\n(tv_symbols.py)", fc="#152040", ec=BORDER, fontsize=9.5)

arrow(ax, (4.0, 4.65), (4.6, 4.65))
arrow(ax, (8.2, 4.65), (8.8, 4.65))

box(ax, 1.5, 1.8, 4.8, 1.3, "Found\nWidget destroyed + rebuilt\nwith the matching symbol", fc="#0d2b1a", ec=GREEN, fontsize=9.5)
box(ax, 6.9, 1.8, 4.8, 1.3, "Not found\n\"[ticker] not found in\nTradingView\" message shown", fc="#2a1220", ec=RED, fontsize=9.5)

arrow(ax, (9.5, 4.0), (5.5, 3.1), connectionstyle="arc3,rad=0.2")
arrow(ax, (10.5, 4.0), (9.5, 3.1), connectionstyle="arc3,rad=-0.2")

ax.text(6.5, 0.9, "Coverage (verified against TradingView's live API, not guessed):\n"
                  "Crypto 50/60 resolve (10 gaps are tokenized institutional funds with no exchange listing) -- Stocks 553/553, 100%.",
        ha="center", fontsize=9.3, color=GREY, style="italic")

save(fig, "08_crypto_stocks_widget.png")

# ---------------------------------------------------------------------------
# 9. SEO / AEO / GEO structure
# ---------------------------------------------------------------------------
fig, ax = new_fig(13, 8)
ax.text(6.5, 7.5, "SEO, AEO & GEO -- How the Site Gets Found", ha="center", fontsize=16,
        fontweight="bold", color=TEXT)

box(ax, 0.4, 5.7, 3.9, 1.2, "SEO\nTraditional search engines\n(Google, Bing)", fc="#152040", ec=GREEN, fontsize=10)
box(ax, 4.7, 5.7, 3.9, 1.2, "AEO\nFeatured snippets,\nvoice assistants", fc="#152040", ec=GOLD, fontsize=10)
box(ax, 9.0, 5.7, 3.6, 1.2, "GEO\nAI chat answers\n(ChatGPT, Perplexity...)", fc="#152040", ec=PURPLE, fontsize=10)

box(ax, 0.2, 3.8, 4.2, 1.4, "sitemap.xml\nEvery page incl. /dubai\nand /metals (previously missing)", fc=BOX_BLUE, ec=GREEN, fontsize=9)
box(ax, 4.6, 3.8, 4.2, 1.4, "JSON-LD Schema\nService, BreadcrumbList,\nFAQPage, ItemList on every\nmajor page", fc=BOX_BLUE, ec=GOLD, fontsize=9)
box(ax, 9.0, 3.8, 3.6, 1.4, "llms.txt\nStructured site map for\nAI crawlers (llmstxt.org)", fc=BOX_BLUE, ec=PURPLE, fontsize=9)

arrow(ax, (2.3, 5.7), (2.3, 5.2))
arrow(ax, (6.65, 5.7), (6.65, 5.2))
arrow(ax, (10.8, 5.7), (10.8, 5.2))

box(ax, 2.5, 1.6, 8.0, 1.4, "Visible FAQ sections (Homepage, Dubai, Metals)\nClear Q&A pairs that double as both on-page content and FAQPage schema",
    fc="#152040", ec=BORDER, fontsize=9.5)
arrow(ax, (2.3, 3.8), (4.5, 3.0), connectionstyle="arc3,rad=0.2")
arrow(ax, (10.8, 3.8), (8.5, 3.0), connectionstyle="arc3,rad=-0.2")

ax.text(6.5, 0.7, "One piece of content, three audiences: the same FAQ answers a Google search,\n"
                  "gets read aloud by a voice assistant, and gets quoted directly by an AI chatbot.",
        ha="center", fontsize=9.3, color=GREY, style="italic")

save(fig, "09_seo_aeo_geo.png")

print("All v2 diagrams generated in", OUT)
