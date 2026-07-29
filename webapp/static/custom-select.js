/* Custom themed dropdown -- replaces native <select> everywhere on the site
   so the opened options panel can actually be styled (rounded corners, dark
   theme, hover highlight). Native <select> only lets you style the closed
   box; the browser renders the open option list itself, ignoring page CSS.

   Usage:
     <div class="custom-select custom-select-block" id="my-select">
       <button type="button" class="custom-select-btn" aria-haspopup="listbox" aria-expanded="false">
         <span class="custom-select-value">Label</span>
       </button>
       <ul class="custom-select-panel" role="listbox" hidden>
         <li role="option" data-value="a" aria-selected="true">Option A</li>
         <li role="option" data-value="b">Option B</li>
       </ul>
     </div>

     initCustomSelect("my-select", (value) => { ... });

   Uses event delegation on the panel (not per-<li> listeners) so options
   can be regenerated via setCustomSelectOptions() -- e.g. a category
   dropdown repopulating a dependent dropdown -- without losing click
   handling on the new items. */

function initCustomSelect(id, onSelect) {
    const root = document.getElementById(id);
    if (!root) return;
    const btn = root.querySelector(".custom-select-btn");
    const valueEl = root.querySelector(".custom-select-value");
    const panel = root.querySelector(".custom-select-panel");

    function closePanel() {
        panel.hidden = true;
        panel.style.display = "none";
        btn.setAttribute("aria-expanded", "false");
    }
    function openPanel() {
        panel.hidden = false;
        panel.style.display = "";
        btn.setAttribute("aria-expanded", "true");
    }

    btn.addEventListener("click", () => {
        if (panel.hidden) openPanel(); else closePanel();
    });

    panel.addEventListener("click", (e) => {
        const opt = e.target.closest("li[role='option']");
        if (!opt || !panel.contains(opt)) return;
        valueEl.textContent = opt.textContent;
        Array.from(panel.querySelectorAll("li[role='option']")).forEach(
            o => o.setAttribute("aria-selected", String(o === opt))
        );
        closePanel();
        onSelect(opt.dataset.value);
    });

    document.addEventListener("click", (e) => {
        if (!root.contains(e.target)) closePanel();
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closePanel();
    });
}

/* Regenerates a custom-select's option list (e.g. when a parent category
   dropdown changes what a dependent dropdown should offer). Selects the
   first option by default and updates the visible label to match. */
function setCustomSelectOptions(id, options) {
    const root = document.getElementById(id);
    if (!root) return;
    const valueEl = root.querySelector(".custom-select-value");
    const panel = root.querySelector(".custom-select-panel");

    panel.innerHTML = options.map((opt, i) =>
        `<li role="option" data-value="${opt.value}"${i === 0 ? ' aria-selected="true"' : ''}>${opt.label}</li>`
    ).join("");

    if (options.length && valueEl) {
        valueEl.textContent = options[0].label;
    }
}
