(() => {
  "use strict";

  const header = document.querySelector("[data-header]");
  const menu = document.querySelector("[data-menu]");
  const nav = document.querySelector("[data-nav]");

  const closeMenu = () => {
    menu.setAttribute("aria-expanded", "false");
    nav.classList.remove("open");
  };

  menu.addEventListener("click", () => {
    const open = menu.getAttribute("aria-expanded") === "true";
    menu.setAttribute("aria-expanded", String(!open));
    nav.classList.toggle("open", !open);
  });
  nav.addEventListener("click", (event) => {
    if (event.target.closest("a")) closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });

  const syncHeader = () => header.classList.toggle("scrolled", window.scrollY > 16);
  syncHeader();
  window.addEventListener("scroll", syncHeader, { passive: true });

  if ("IntersectionObserver" in window && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("visible");
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.14 });
    document.querySelectorAll(".reveal").forEach((element) => observer.observe(element));
  } else {
    document.querySelectorAll(".reveal").forEach((element) => element.classList.add("visible"));
  }
})();
