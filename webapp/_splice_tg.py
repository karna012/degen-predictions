# Splice telegram section out of admin_dashboard.html and insert includes.
from pathlib import Path

p = Path(r"F:\Api_claude_projects\pro2\Degen_predictions\webapp\templates\admin_dashboard.html")
text = p.read_text(encoding="utf-8")
marker = 'data-admin-section="telegram"'
pos = text.find(marker)
if pos < 0:
    raise SystemExit("telegram marker not found")
start = text.rfind("<section", 0, pos)
if start < 0:
    raise SystemExit("section start not found")

# Keep through combo_results; drop old telegram + scripts
head = text[:start].rstrip() + "\n\n"
tail = (
    '{% include "partials/admin_telegram_sender.html" %}\n\n'
    "{% endblock %}\n\n"
    "{% block admin_scripts %}\n"
    '{% include "partials/admin_telegram_scripts.html" %}\n'
    "{% endblock %}\n"
)
# Also refresh overview card blurb
head = head.replace(
    "<p>Pick legs / combo slips and post slip images to the channel (no %).</p>",
    "<p>Sport → League → Event → Markets betslip builder · send to channel (no %).</p>",
)
p.write_text(head + tail, encoding="utf-8")
print("ok", p.stat().st_size)
