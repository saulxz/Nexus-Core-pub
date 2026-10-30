/*
 * Shell de la dirección visual 2026.
 * Mantiene intactos los contratos de app.js: solo mueve nodos existentes,
 * añade navegación alternativa y decora las tablas de Hosts.
 */
(function () {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const SECTION_LABELS = {
    dashboard: "Resumen",
    hosts: "Hosts",
    alerts: "Alertas",
    monitors: "Monitores",
    containers: "Contenedores",
    services: "Servicios",
    network: "Red",
    system: "Sistema",
    history: "Histórico",
    audit: "Auditoría",
    security: "Seguridad",
    terminal: "Terminal",
    chat: "Asistente",
  };

  function createIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons({ attrs: { "stroke-width": 1.6 } });
    }
  }

  function currentHostName() {
    const select = $("#host-select");
    const option = select && select.options[select.selectedIndex];
    return option ? option.textContent.replace(/\s+·\s+agente\s+#\d+$/i, "").trim() : "Este servidor";
  }

  function updateBreadcrumb(section) {
    const sectionNode = $("#nx-crumb-section");
    const hostNode = $("#nx-crumb-host");
    if (sectionNode) sectionNode.textContent = SECTION_LABELS[section] || section;
    if (hostNode) hostNode.textContent = currentHostName();
  }

  function updateAlertBadge() {
    const source = $("#count-crit");
    const badge = $("#nx-side-alerts-count");
    if (!source || !badge) return;
    const value = source.textContent.trim();
    const count = Number.parseInt(value, 10);
    badge.textContent = Number.isFinite(count) && count > 0 ? String(count) : "";
    badge.title = Number.isFinite(count) && count > 0 ? `${count} alarmas críticas` : "";
  }

  function moveShellNodes() {
    const hostsel = $(".nx-hostsel");
    const topbarHost = $("#nx-topbar-host");
    if (hostsel && topbarHost && hostsel.parentElement !== topbarHost) {
      topbarHost.appendChild(hostsel);
    }

    const statusbar = $("#status-bar");
    const statusSlot = $("#nx-dashboard-status");
    if (statusbar && statusSlot && statusbar.parentElement !== statusSlot) {
      statusSlot.appendChild(statusbar);
    }
  }

  function setupDrawer() {
    const sidebar = $("#nx-sidebar");
    const button = $("#nx-menu-btn");
    const backdrop = $("#nx-side-backdrop");
    if (!sidebar || !button) return;

    const setOpen = (open) => {
      sidebar.classList.toggle("is-open", open);
      button.setAttribute("aria-expanded", String(open));
      if (backdrop) backdrop.hidden = !open;
    };

    button.addEventListener("click", () => setOpen(!sidebar.classList.contains("is-open")));
    $("#nx-side-logout")?.addEventListener("click", () => $("#logout-btn")?.click());
    if (backdrop) backdrop.addEventListener("click", () => setOpen(false));
    $$(".nx-side-link").forEach((link) => link.addEventListener("click", () => setOpen(false)));
    window.addEventListener("nx-tab", () => setOpen(false));
  }

  function sectionItems() {
    return $$(".nx-side-link").map((button) => ({
      kind: "Sección",
      label: button.textContent.trim(),
      icon: button.querySelector("svg")?.dataset.lucide || "chevron-right",
      tab: button.dataset.tab,
      element: button,
    }));
  }

  function hostItems() {
    const select = $("#host-select");
    if (!select) return [];
    return Array.from(select.options).map((option) => ({
      kind: "Host",
      label: option.textContent.trim(),
      icon: "server",
      hostId: option.value,
      element: select,
    }));
  }

  function setupSearch() {
    const overlay = $("#nx-command");
    const input = $("#nx-command-input");
    const list = $("#nx-command-list");
    const trigger = $("#nx-search-trigger");
    if (!overlay || !input || !list) return;

    let items = [];
    let activeIndex = 0;

    const render = (query) => {
      const needle = query.trim().toLowerCase();
      const filtered = items.filter((item) => !needle || item.label.toLowerCase().includes(needle));
      activeIndex = Math.min(activeIndex, Math.max(0, filtered.length - 1));
      list.innerHTML = "";
      if (!filtered.length) {
        list.innerHTML = '<div class="nx-command-empty">Sin resultados</div>';
        return;
      }
      filtered.forEach((item, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `nx-command-item${index === activeIndex ? " is-active" : ""}`;
        button.innerHTML = `<i data-lucide="${item.icon}" aria-hidden="true"></i><span>${item.label}</span><span class="nx-command-kind">${item.kind}</span>`;
        button.addEventListener("click", () => choose(item));
        list.appendChild(button);
      });
      createIcons();
    };

    const choose = (item) => {
      if (item.tab) {
        const button = document.querySelector(`.nx-side-link[data-tab="${item.tab}"]`);
        if (button) button.click();
      } else if (item.hostId) {
        const select = $("#host-select");
        if (select) {
          select.value = item.hostId;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        const hosts = document.querySelector('.nx-side-link[data-tab="hosts"]');
        if (hosts) hosts.click();
      }
      close();
    };

    const open = () => {
      items = [...sectionItems(), ...hostItems()];
      activeIndex = 0;
      input.value = "";
      overlay.hidden = false;
      render("");
      window.requestAnimationFrame(() => input.focus());
    };

    const close = () => {
      overlay.hidden = true;
      if (trigger) trigger.focus();
    };

    trigger?.addEventListener("click", open);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close();
    });
    input.addEventListener("input", () => render(input.value));
    input.addEventListener("keydown", (event) => {
      const filtered = items.filter((item) => !input.value.trim() || item.label.toLowerCase().includes(input.value.trim().toLowerCase()));
      if (event.key === "Escape") { event.preventDefault(); close(); return; }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        activeIndex = Math.min(activeIndex + 1, filtered.length - 1);
        render(input.value);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        activeIndex = Math.max(activeIndex - 1, 0);
        render(input.value);
        return;
      }
      if (event.key === "Enter" && filtered[activeIndex]) {
        event.preventDefault();
        choose(filtered[activeIndex]);
      }
    });
    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        open();
      }
      if (event.key === "Escape" && !overlay.hidden) close();
    });
  }

  function setupBreadcrumb() {
    window.addEventListener("nx-tab", (event) => updateBreadcrumb(event.detail.name));
    const select = $("#host-select");
    if (select) {
      select.addEventListener("change", () => updateBreadcrumb(document.body.dataset.tab || "dashboard"));
    }
    updateBreadcrumb(document.body.dataset.tab || "dashboard");
  }

  function availabilityBar(online) {
    const total = 32;
    const segments = Array.from({ length: total }, (_, index) => {
      const isDown = !online && index > 42;
      return `<i class="nx-avail-seg ${isDown ? "is-down" : ""}"></i>`;
    }).join("");
    return `<span class="nx-avail-bar" role="img" aria-label="${online ? "Disponible las últimas 24 horas" : "Sin latido; caída reciente"}">${segments}</span>`;
  }

  function addAvailabilityHeader() {
    const head = $("#hosts-table-wrap thead tr");
    if (!head || head.querySelector(".nx-th-avail")) return;
    const th = document.createElement("th");
    th.className = "nx-th-avail";
    th.scope = "col";
    th.textContent = "Disponibilidad · 24 h";
    const last = head.lastElementChild;
    head.insertBefore(th, last);
  }

  function decorateHostRows() {
    addAvailabilityHeader();
    const tbody = $("#hosts-table");
    if (!tbody) return;
    $$("tr", tbody).forEach((row) => {
      if (row.querySelector(".nx-avail-cell") || !row.querySelector("td") || row.querySelector("td[colspan]")) return;
      const online = Boolean(row.querySelector(".nx-led--ok, .nx-led--on"));
      const statusCell = row.querySelector("td:nth-child(2)");
      if (statusCell && !statusCell.querySelector(".nx-host-status")) {
        const status = document.createElement("span");
        status.className = `nx-host-status${online ? "" : " is-down"}`;
        status.textContent = online ? "En línea" : "Sin latido";
        statusCell.appendChild(status);
      }
      const cell = document.createElement("td");
      cell.className = "nx-avail-cell";
      cell.innerHTML = availabilityBar(online);
      const last = row.lastElementChild;
      row.insertBefore(cell, last);
    });
  }

  function setupHostAvailability() {
    const tbody = $("#hosts-table");
    if (!tbody) return;
    decorateHostRows();
    new MutationObserver(decorateHostRows).observe(tbody, { childList: true });
  }

  function setupAlertBadge() {
    const source = $("#count-crit");
    if (!source) return;
    updateAlertBadge();
    new MutationObserver(updateAlertBadge).observe(source, { childList: true, characterData: true, subtree: true });
  }

  function boot() {
    moveShellNodes();
    setupDrawer();
    setupSearch();
    setupBreadcrumb();
    setupHostAvailability();
    setupAlertBadge();
    createIcons();
    updateBreadcrumb(document.body.dataset.tab || "dashboard");
    const initial = document.querySelector('.nx-side-link.tab-active');
    if (!initial) {
      const dashboard = document.querySelector('.nx-side-link[data-tab="dashboard"]');
      dashboard?.classList.add("tab-active");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
