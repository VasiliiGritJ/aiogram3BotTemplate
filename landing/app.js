(() => {
  "use strict";

  const year = document.querySelector("#year");
  if (year) year.textContent = String(new Date().getFullYear());

  const telegramUrl = document.documentElement.dataset.telegramUrl;
  if (/^https:\/\/(?:www\.)?(?:t\.me|telegram\.me)\/[^/?#]+$/i.test(telegramUrl || "")) {
    for (const cta of document.querySelectorAll("[data-telegram-cta]")) {
      cta.href = telegramUrl;
      cta.target = "_blank";
      cta.rel = "noopener noreferrer";
    }
  }

  for (const link of document.querySelectorAll('a[href^="#"]')) {
    link.addEventListener("click", (event) => {
      const target = document.querySelector(link.getAttribute("href"));
      if (!target) return;
      event.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      if (target.tabIndex < 0) target.tabIndex = -1;
    });
  }
})();
