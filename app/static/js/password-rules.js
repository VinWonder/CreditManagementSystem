(() => {
  // Mirrors strong_password in app/auth/forms.py; the server remains the authority.
  const RULES = {
    length: (value) => value.length >= 8 && value.length <= 128,
    upper: (value) => /[A-Z]/.test(value),
    lower: (value) => /[a-z]/.test(value),
    number: (value) => /\d/.test(value),
    symbol: (value) => /[^A-Za-z0-9\s]/.test(value),
    nospace: (value) => !/\s/.test(value),
  };

  document.querySelectorAll("[data-password-rules]").forEach((list) => {
    const input = list.parentElement.querySelector('input[type="password"]');
    if (!input) {
      return;
    }

    const update = () => {
      list.querySelectorAll("[data-rule]").forEach((item) => {
        const met = RULES[item.dataset.rule](input.value);
        item.classList.toggle("is-met", met);
        item.querySelector("i").className = met ? "bi bi-check-circle-fill" : "bi bi-circle";
      });
    };

    input.addEventListener("input", update);
    update();
  });
})();
