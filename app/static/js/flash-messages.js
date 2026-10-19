(() => {
  document.querySelectorAll("[data-confirm-message]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirmMessage)) {
        event.preventDefault();
      }
    });
  });

  document.querySelectorAll(".app-flash").forEach((notice) => {
    const dismiss = () => {
      notice.classList.add("is-dismissing");
      window.setTimeout(() => notice.remove(), 220);
    };

    notice.querySelector(".app-flash-close")?.addEventListener("click", dismiss);
    if (notice.classList.contains("app-flash-auto-dismiss")) {
      window.setTimeout(dismiss, 20000);
    }
  });
})();
