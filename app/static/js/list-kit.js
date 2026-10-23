(() => {
  // History lists: "Show more" loads the next batch in place, a day (or
  // letter) split across batches is joined back into one group, and the
  // sticky group headings sit just under the app's top bar.
  const navbar = document.querySelector(".app-navbar");
  function setStickyOffset() {
    if (navbar) document.documentElement.style.setProperty("--list-sticky-top", `${navbar.offsetHeight}px`);
  }
  setStickyOffset();
  window.addEventListener("resize", setStickyOffset);

  document.querySelectorAll("select[data-auto-submit]").forEach((select) => {
    select.addEventListener("change", () => select.form && select.form.requestSubmit());
  });

  function mergeBatch(list, html) {
    const holder = document.createElement("div");
    holder.innerHTML = html;
    let firstNew = null;
    holder.querySelectorAll(".daybook-group").forEach((group) => {
      const groups = list.querySelectorAll(".daybook-group");
      const last = groups[groups.length - 1];
      if (last && last.dataset.groupKey === group.dataset.groupKey) {
        const rows = last.querySelector(".daybook-rows");
        group.querySelectorAll(".daybook-row").forEach((row) => {
          rows.appendChild(row);
          firstNew = firstNew || row;
        });
      } else {
        list.appendChild(group);
        firstNew = firstNew || group.querySelector(".daybook-row");
      }
    });
    return { meta: holder.querySelector("[data-batch-meta]"), firstNew };
  }

  document.querySelectorAll("[data-daybook-more]").forEach((more) => {
    const list = more.previousElementSibling && more.previousElementSibling.matches("[data-daybook]")
      ? more.previousElementSibling
      : document.querySelector("[data-daybook]");
    const button = more.querySelector("[data-show-more]");
    if (!list || !button) return;

    button.addEventListener("click", async (event) => {
      event.preventDefault();
      if (button.getAttribute("aria-busy") === "true") return;
      const label = button.querySelector("span");
      button.setAttribute("aria-busy", "true");
      label.textContent = "Loading…";
      try {
        const url = new URL(button.href, window.location.href);
        url.searchParams.set("partial", "1");
        const response = await fetch(url, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const { meta, firstNew } = mergeBatch(list, await response.text());
        if (meta) {
          more.querySelector("[data-shown]").textContent = meta.dataset.shown;
          if (meta.dataset.next) {
            button.href = meta.dataset.next;
            label.textContent = `Show ${meta.dataset.nextCount} more`;
          } else {
            button.remove();
          }
        } else {
          // Not a list batch (for example the session ended): load the page normally.
          window.location.href = button.href;
          return;
        }
        // Keep keyboard and screen-reader users where the new rows start.
        const target = firstNew && firstNew.querySelector("a, button");
        if (target) target.focus({ preventScroll: true });
      } catch (error) {
        // Fall back to an ordinary page load of the longer list.
        window.location.href = button.href;
        return;
      } finally {
        button.removeAttribute("aria-busy");
      }
    });
  });

  document.querySelectorAll("[data-back-to-top]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    });
  });

  // Long lists inside a page (a customer's sales and payments) show the
  // newest few and offer "Show all".
  document.querySelectorAll("[data-collapse-rows]").forEach((container) => {
    const keep = Number(container.dataset.collapseRows) || 5;
    const rows = [...container.querySelectorAll("[data-collapse-row]")];
    if (rows.length <= keep + 1) return;
    rows.slice(keep).forEach((row) => { row.hidden = true; });
    const button = document.createElement("button");
    button.type = "button";
    button.className = "collapse-rows-toggle";
    button.setAttribute("aria-expanded", "false");
    const noun = container.dataset.collapseNoun || "entries";
    const setLabel = (open) => {
      button.innerHTML = open
        ? `<i class="bi bi-chevron-up" aria-hidden="true"></i> Show fewer`
        : `<i class="bi bi-chevron-down" aria-hidden="true"></i> Show all ${rows.length} ${noun}`;
    };
    setLabel(false);
    button.addEventListener("click", () => {
      const open = button.getAttribute("aria-expanded") !== "true";
      rows.slice(keep).forEach((row) => { row.hidden = !open; });
      button.setAttribute("aria-expanded", String(open));
      setLabel(open);
    });
    // A button cannot sit inside a table, so it goes after the whole table.
    const anchor = container.matches("tbody") ? container.closest(".table-responsive") || container.closest("table") : container;
    anchor.insertAdjacentElement("afterend", button);
    window.addEventListener("beforeprint", () => rows.forEach((row) => { row.hidden = false; }));
  });

  // Keep the chosen day (or letter) in view in strips that scroll sideways on phones.
  document.querySelectorAll(".daystrip-bars, .letter-jump").forEach((strip) => {
    const chosen = strip.querySelector(".is-selected, .is-active") || (strip.matches(".daystrip-bars") ? strip.lastElementChild : null);
    if (!chosen) return;
    const overflow = chosen.getBoundingClientRect().right - strip.getBoundingClientRect().right;
    if (overflow > 0) strip.scrollLeft += overflow + 8;
  });
})();
