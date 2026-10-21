(() => {
  document.querySelectorAll("[data-confirm-message]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirmMessage)) {
        event.preventDefault();
      }
    });
  });

  const AUTO_DISMISS_MS = 5000;

  document.querySelectorAll(".app-flash").forEach((notice) => {
    const dismiss = () => {
      notice.classList.add("is-dismissing");
      window.setTimeout(() => notice.remove(), 220);
    };

    notice.querySelector(".app-flash-close")?.addEventListener("click", dismiss);
    if (!notice.classList.contains("app-flash-auto-dismiss")) {
      return;
    }

    // Pause the countdown while the pointer rests on the notice so it can be read.
    let remaining = AUTO_DISMISS_MS;
    let startedAt = Date.now();
    let timer = window.setTimeout(dismiss, remaining);
    notice.addEventListener("mouseenter", () => {
      window.clearTimeout(timer);
      remaining -= Date.now() - startedAt;
      notice.classList.add("is-paused");
    });
    notice.addEventListener("mouseleave", () => {
      startedAt = Date.now();
      timer = window.setTimeout(dismiss, Math.max(remaining, 0));
      notice.classList.remove("is-paused");
    });
  });
})();
