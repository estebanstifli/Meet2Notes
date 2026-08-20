(() => {
  "use strict";

  const storageKey = "meet2notes-ui-theme";
  let preference = "system";
  try {
    const saved = window.localStorage.getItem(storageKey);
    if (["system", "light", "dark"].includes(saved)) preference = saved;
  } catch (_error) {
    // The persistent API preference will be applied once the application loads.
  }
  const resolved = preference === "system"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : preference;
  document.documentElement.dataset.themePreference = preference;
  document.documentElement.dataset.theme = resolved;
  try {
    if (window.localStorage.getItem("meet2notes-sidebar-collapsed") === "true"
        && window.matchMedia("(min-width: 821px)").matches) {
      document.documentElement.classList.add("sidebar-collapsed");
    }
  } catch (_error) {
    // The sidebar defaults to expanded when browser storage is unavailable.
  }
})();
