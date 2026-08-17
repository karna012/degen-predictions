(function () {
  const shell = document.getElementById("admin-shell");
  const menuBtn = document.getElementById("admin-menu-btn");
  const backdrop = document.getElementById("admin-backdrop");
  const titleEl = document.getElementById("admin-section-title");
  const navLinks = Array.from(document.querySelectorAll("[data-admin-section-link]"));
  const sections = Array.from(document.querySelectorAll("[data-admin-section]"));
  const filterField = document.getElementById("filter-section-field");

  function currentFilter() {
    const params = new URLSearchParams(window.location.search);
    return params.get("filter") || "all";
  }

  function setSection(id, pushUrl) {
    const target = id || "overview";
    sections.forEach((sec) => {
      sec.classList.toggle("is-visible", sec.dataset.adminSection === target);
    });
    navLinks.forEach((link) => {
      link.classList.toggle("is-active", link.dataset.adminSectionLink === target);
    });
    const active = navLinks.find((l) => l.dataset.adminSectionLink === target);
    if (titleEl && active) {
      titleEl.textContent = active.dataset.title || active.textContent.trim();
    }
    if (filterField) filterField.value = target;
    if (pushUrl && sections.length) {
      const url = new URL(window.location.href);
      url.searchParams.set("section", target);
      if (!url.searchParams.get("filter")) {
        url.searchParams.set("filter", currentFilter());
      }
      window.history.replaceState({}, "", url);
    }
    if (shell) shell.classList.remove("sidebar-open");
  }

  // Full page loads per section — each /admin?section= request only
  // gathers that panel's data. Do not SPA-switch (that forced one
  // request to load picks + analytics + geoip + telegram markets).

  if (menuBtn && shell) {
    menuBtn.addEventListener("click", () => shell.classList.toggle("sidebar-open"));
  }
  if (backdrop && shell) {
    backdrop.addEventListener("click", () => shell.classList.remove("sidebar-open"));
  }

  if (sections.length) {
    const params = new URLSearchParams(window.location.search);
    setSection(params.get("section") || "overview", false);
  }

  document.querySelectorAll(".js-table-search").forEach((input) => {
    const tableId = input.dataset.table;
    const table = document.getElementById(tableId);
    if (!table) return;
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      table.querySelectorAll("tbody tr").forEach((row) => {
        row.style.display = !q || row.innerText.toLowerCase().includes(q) ? "" : "none";
      });
    });
  });

  document.querySelectorAll(".feedback-msg").forEach((el) => {
    el.addEventListener("click", () => {
      el.style.maxHeight = el.style.maxHeight === "none" ? "4.5em" : "none";
    });
  });
})();
