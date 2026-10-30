/*
 * Tema claro/oscuro compartido por el panel web y la app de escritorio.
 * - "system" sigue la preferencia del sistema; el botón alterna claro/oscuro.
 * - Se aplica antes de pintar para evitar el flash de tema.
 */
(function () {
  "use strict";

  const STORAGE_KEY = "nexus-theme";
  const THEME_COLORS = {
    dark: "#101113",
    light: "#f5f4f0",
  };
  const ICONS = {
    dark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.5 14.2A8.5 8.5 0 0 1 9.8 3.5 8.5 8.5 0 1 0 20.5 14.2Z"/></svg>',
    light: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3.6"/><path d="M12 2.8v2.1M12 19.1v2.1M2.8 12h2.1M19.1 12h2.1M5.5 5.5 7 7M17 17l1.5 1.5M18.5 5.5 17 7M7 17l-1.5 1.5"/></svg>',
  };
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  let mode = "system";

  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "system") mode = saved;
  } catch (_) {
    /* localStorage puede estar bloqueado; se usa system */
  }

  function resolvedTheme() {
    return mode === "system" ? (media.matches ? "dark" : "light") : mode;
  }

  function syncButtons() {
    const dark = resolvedTheme() === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.innerHTML = dark ? ICONS.light : ICONS.dark;
      button.title = dark ? "Cambiar a tema claro" : "Cambiar a tema oscuro";
      button.setAttribute(
        "aria-label",
        dark ? "Cambiar a tema claro" : "Cambiar a tema oscuro",
      );
      button.setAttribute("aria-pressed", String(!dark));
    });
  }

  function apply() {
    const theme = resolvedTheme();
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", THEME_COLORS[theme]);
    syncButtons();
    window.dispatchEvent(
      new CustomEvent("nx-theme-change", { detail: { theme, mode } }),
    );
  }

  function setMode(next) {
    if (next !== "light" && next !== "dark" && next !== "system") return;
    mode = next;
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
    } catch (_) {
      /* sin persistencia */
    }
    apply();
  }

  function toggle() {
    setMode(resolvedTheme() === "dark" ? "light" : "dark");
  }

  function bind() {
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      if (button.dataset.themeBound === "1") return;
      button.dataset.themeBound = "1";
      button.addEventListener("click", toggle);
    });
    syncButtons();
  }

  media.addEventListener("change", () => {
    if (mode === "system") apply();
  });

  document.addEventListener("DOMContentLoaded", bind);
  apply();

  window.NexusTheme = {
    get mode() {
      return mode;
    },
    get theme() {
      return resolvedTheme();
    },
    setMode,
    toggle,
    apply,
  };
})();

