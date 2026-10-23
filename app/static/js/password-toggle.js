(() => {
  // Give every password box a show/hide button, unless the page already has one.
  const withToggle = new Set(
    [...document.querySelectorAll("[data-toggle-password]")]
      .map((button) => button.closest(".password-input")?.querySelector("input"))
      .filter(Boolean)
  );

  document.querySelectorAll('input[type="password"]').forEach((input) => {
    if (withToggle.has(input) || input.closest("template")) return;

    const wrap = document.createElement("span");
    wrap.className = "password-reveal";
    input.parentNode.insertBefore(wrap, input);
    wrap.append(input);

    const button = document.createElement("button");
    button.type = "button";
    button.className = "password-reveal-button";
    button.setAttribute("aria-label", "Show password");
    button.innerHTML = '<i class="bi bi-eye" aria-hidden="true"></i>';
    button.addEventListener("click", () => {
      const showing = input.type === "text";
      input.type = showing ? "password" : "text";
      button.setAttribute("aria-label", showing ? "Show password" : "Hide password");
      button.querySelector("i").className = showing ? "bi bi-eye" : "bi bi-eye-slash";
      input.focus();
    });
    wrap.append(button);
  });

  // Never submit a password as visible text that the browser might remember.
  document.addEventListener("submit", (event) => {
    event.target.querySelectorAll(".password-reveal input, .password-input input").forEach((input) => {
      if (input.type === "text" && input.autocomplete !== "username") input.type = "password";
    });
  }, true);
})();
