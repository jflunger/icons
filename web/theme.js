/* Sets data-theme on <html> before first paint: the saved choice, else the OS preference. */
(function () {
  let t = null;
  try { t = JSON.parse(localStorage.getItem("iconsearch.theme")); } catch { /* private mode etc. */ }
  if (t !== "light" && t !== "dark") t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = t;
})();
