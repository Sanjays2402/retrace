(() => {
  const root = document.documentElement;
  try {
    const theme = localStorage.getItem("retrace-theme");
    root.dataset.theme = theme === "light" || theme === "dark"
      ? theme
      : matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    const accent = localStorage.getItem("retrace-accent");
    if (["green", "red", "yellow", "blue"].includes(accent)) root.dataset.accent = accent;
  } catch {
    root.dataset.theme = "light";
  }
  addEventListener("DOMContentLoaded", () => {
    document.querySelector(".theme-button")?.addEventListener("click", () => {
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("retrace-theme", next); } catch { /* optional */ }
    });
  });
})();
