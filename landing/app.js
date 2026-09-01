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

  const trainingStates = {
    gym: {
      mode: "ТРЕНАЖЁРНЫЙ ЗАЛ",
      day: "ПН / НИЗ ТЕЛА",
      status: "STATUS: READY",
      duration: "60:00",
      meta: "GYM / 03",
      next: "СЛЕДУЮЩЕЕ: РАЗМИНКА",
      progress: "42%",
      grid: "gym-grid",
      exercises: [["Жим ногами", "3 × 8–12"], ["Сгибание ног лёжа", "3 × 10–15"], ["Жим в тренажёре", "3 × 8–12"]],
    },
    functional: {
      mode: "ФУНКЦИОНАЛЬНЫЙ ЗАЛ",
      day: "СР / FULL BODY",
      status: "STATUS: ADAPTIVE",
      duration: "45:00",
      meta: "FUNCTIONAL / 02",
      next: "СЛЕДУЮЩЕЕ: РАБОЧИЙ КРУГ",
      progress: "58%",
      grid: "functional-grid",
      exercises: [["Гоблет-присед", "3 × 8–12"], ["Тяга в наклоне", "3 × 8–12"], ["Фермерская прогулка", "3 × 30 сек"]],
    },
    street: {
      mode: "УЛИЦА / ТУРНИК",
      day: "СР / ВЕРХ ТЕЛА",
      status: "STATUS: OUTDOOR",
      duration: "45:00",
      meta: "STREET / 03",
      next: "СЛЕДУЮЩЕЕ: ТУРНИК",
      progress: "67%",
      grid: "street-grid",
      exercises: [["Подтягивания", "3 × 6–10"], ["Отжимания на брусьях", "3 × 6–10"], ["Выпады", "3 × 10–12"]],
    },
    home: {
      mode: "ДОМ / БЕЗ ОБОРУДОВАНИЯ",
      day: "ПТ / FULL BODY",
      status: "STATUS: HOME MODE",
      duration: "30:00",
      meta: "HOME / 01",
      next: "СЛЕДУЮЩЕЕ: ПЕРВЫЙ КРУГ",
      progress: "33%",
      grid: "home-grid",
      exercises: [["Приседания с паузой", "3 × 10–15"], ["Отжимания", "3 × 8–15"], ["Ягодичный мост", "3 × 12–15"]],
    },
  };

  const stateElements = {
    mode: document.querySelector("#training-mode"),
    day: document.querySelector("#training-day"),
    status: document.querySelector("#training-status"),
    duration: document.querySelector("#training-duration"),
    meta: document.querySelector("#training-meta"),
    next: document.querySelector("#training-next"),
    progress: document.querySelector("#training-progress"),
    screen: document.querySelector("#training-screen"),
    exerciseStack: document.querySelector("#exercise-stack"),
  };
  const trainingTabs = document.querySelectorAll("[data-training-tab]");
  const environmentCards = document.querySelectorAll("[data-environment-mode]");

  function updateTabs(key) {
    for (const tab of trainingTabs) {
      tab.setAttribute("aria-selected", String(tab.dataset.trainingTab === key));
    }
    for (const card of environmentCards) {
      card.setAttribute("aria-pressed", String(card.dataset.environmentMode === key));
    }
  }

  function renderTrainingState(key) {
    const state = trainingStates[key];
    if (!state || !stateElements.exerciseStack) return;

    stateElements.mode.textContent = state.mode;
    stateElements.day.textContent = state.day;
    stateElements.status.textContent = state.status;
    stateElements.duration.textContent = state.duration;
    stateElements.meta.textContent = state.meta;
    stateElements.next.textContent = state.next;
    stateElements.progress.style.width = state.progress;
    stateElements.screen.dataset.grid = state.grid;
    stateElements.exerciseStack.replaceChildren(
      ...state.exercises.map(([exercise, prescription], index) => {
        const row = document.createElement("div");
        row.className = `exercise-row${index === 0 ? " active" : ""}`;
        const number = document.createElement("span");
        number.textContent = `0${index + 1}`;
        const name = document.createElement("strong");
        name.textContent = exercise;
        const details = document.createElement("b");
        details.textContent = prescription;
        row.append(number, name, details);
        return row;
      }),
    );
    updateTabs(key);
  }

  for (const tab of trainingTabs) {
    tab.addEventListener("click", () => renderTrainingState(tab.dataset.trainingTab));
  }
  for (const card of environmentCards) {
    const key = card.dataset.environmentMode;
    card.addEventListener("click", () => renderTrainingState(key));
    card.addEventListener("focus", () => renderTrainingState(key));
    if (window.matchMedia("(pointer: fine)").matches) {
      card.addEventListener("pointerenter", () => renderTrainingState(key));
    }
  }

  const chaosWindow = document.querySelector("[data-chaos-system]");
  const chaosToggle = document.querySelector("[data-chaos-toggle]");
  const chaosLabel = document.querySelector("[data-chaos-label]");
  if (chaosWindow && chaosToggle && chaosLabel) {
    chaosToggle.addEventListener("click", () => {
      const isSystem = chaosWindow.classList.toggle("is-system");
      chaosToggle.setAttribute("aria-pressed", String(isSystem));
      chaosToggle.textContent = isSystem ? "СИСТЕМА СОБРАНА" : "СОБРАТЬ СИСТЕМУ";
      chaosLabel.textContent = isSystem ? "STATUS: SYSTEM READY" : "STATUS: SIGNAL LOST";
    });
    if (!("IntersectionObserver" in window)) {
      chaosWindow.classList.add("is-visible");
    } else {
      new IntersectionObserver((entries, observer) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        chaosWindow.classList.add("is-visible");
        observer.disconnect();
      }, { threshold: 0.22 }).observe(chaosWindow);
    }
  }

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const hasFinePointer = window.matchMedia("(pointer: fine)").matches;
  if (!reduceMotion && hasFinePointer) {
    for (const panel of document.querySelectorAll("[data-reactive]")) {
      panel.addEventListener("pointermove", (event) => {
        const bounds = panel.getBoundingClientRect();
        const x = (event.clientX - bounds.left) / bounds.width - 0.5;
        const y = (event.clientY - bounds.top) / bounds.height - 0.5;
        panel.style.setProperty("--rx", `${-y * 2.2}deg`);
        panel.style.setProperty("--ry", `${x * 2.2}deg`);
      });
      panel.addEventListener("pointerleave", () => {
        panel.style.removeProperty("--rx");
        panel.style.removeProperty("--ry");
      });
    }

    const cursor = document.querySelector(".os-cursor");
    const cursorLabel = cursor?.querySelector("span");
    if (cursor && cursorLabel) {
      document.body.classList.add("has-os-cursor");
      document.addEventListener("pointermove", (event) => {
        cursor.style.transform = `translate3d(${event.clientX}px, ${event.clientY}px, 0)`;
      });
      for (const item of document.querySelectorAll("a, button, [data-cursor]")) {
        item.addEventListener("pointerenter", () => {
          cursor.classList.add("is-active");
          cursorLabel.textContent = item.dataset.cursor || "SELECT";
        });
        item.addEventListener("pointerleave", () => cursor.classList.remove("is-active"));
      }
    }
  }
})();
