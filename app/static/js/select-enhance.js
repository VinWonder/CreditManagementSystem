(() => {
  // Styled dropdowns on every device. On small screens the list opens as a
  // panel over a dimmed page with large rows for tapping. Every page still
  // works with plain <select>s if Tom Select did not load (for example offline).
  const SMALL_SCREEN = window.matchMedia("(max-width: 767.98px)");
  if (typeof window.TomSelect === "undefined") {
    window.DukaSelect = { enhance() {}, sync() {} };
    return;
  }

  const METHOD_ICONS = {
    cash: "bi-cash-stack",
    bank: "bi-bank",
    agent: "bi-shop-window",
    mpesa_send: "bi-phone",
    mpesa_till: "bi-phone",
    mpesa_paybill: "bi-phone",
    mpesa_stk: "bi-phone-vibrate",
    cheque: "bi-journal-check",
  };
  // "Amina Hassan (+254722111222) — KES 2,800.00 due" as used in customer pickers.
  const CUSTOMER_LABEL = /^(.*?) \((\+?[\d ]+)\)(?: — (.*))?$/;
  const SEARCH_FROM = 10;

  function parts(select, data, escape) {
    const text = escape(data.text);
    if (data.value === "") {
      return { html: `<span class="duka-option-empty">${text}</span>` };
    }
    if (select.name === "customer_id") {
      const match = data.text.match(CUSTOMER_LABEL);
      if (match) {
        const extra = [match[2], match[3]].filter(Boolean).map(escape).join(" · ");
        return {
          html: `<span class="duka-option-text"><strong>${escape(match[1])}</strong><small>${extra}</small></span>`,
          item: `<span class="duka-option-text"><strong>${escape(match[1])}</strong></span>`,
        };
      }
    }
    const icon = select.name === "payment_method" ? METHOD_ICONS[data.value] : null;
    const iconHtml = icon ? `<i class="bi ${icon} duka-option-icon" aria-hidden="true"></i>` : "";
    return { html: `${iconHtml}<span class="duka-option-text">${text}</span>` };
  }

  // Small screens: dim the page behind the open list; tapping the dim area
  // closes the list without pressing whatever is underneath.
  let backdrop = null;

  function panelTitle(select) {
    // Read before Tom Select re-points the label at its own control.
    const label = select.id ? document.querySelector(`label[for="${select.id}"]`) : null;
    return (label && label.textContent.trim()) || select.getAttribute("aria-label") || "Choose an option";
  }

  function openPanel(control, title) {
    if (!SMALL_SCREEN.matches) return;
    control.dropdown.dataset.title = title;
    backdrop = document.createElement("div");
    backdrop.className = "duka-select-backdrop";
    backdrop.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      control.close();
      control.blur();
    });
    document.body.append(backdrop);
    document.body.classList.add("duka-select-open");
  }

  function closePanel() {
    if (backdrop) backdrop.remove();
    backdrop = null;
    document.body.classList.remove("duka-select-open");
  }

  function enhance(root = document) {
    root.querySelectorAll("select:not([multiple]):not([data-native-select])").forEach((select) => {
      if (select.tomselect || select.closest(".iti")) return;
      // Customer lists grow with the shop, so they always get a search box.
      const searchable = select.name === "customer_id" || select.options.length > SEARCH_FROM;
      const compact = !select.classList.contains("form-select");
      const title = panelTitle(select);
      new window.TomSelect(select, {
        create: false,
        allowEmptyOption: true,
        maxOptions: null,
        dropdownParent: "body",
        controlInput: searchable ? undefined : null,
        plugins: searchable ? ["dropdown_input"] : [],
        searchField: ["text"],
        placeholder: searchable ? "Type to search…" : undefined,
        wrapperClass: `ts-wrapper duka-select${compact ? " is-compact" : ""}`,
        dropdownClass: "ts-dropdown duka-dropdown",
        onDropdownOpen() { openPanel(this, title); },
        onDropdownClose() { closePanel(); },
        render: {
          option: (data, escape) => `<div class="duka-option">${parts(select, data, escape).html}</div>`,
          item: (data, escape) => {
            const rendered = parts(select, data, escape);
            return `<div class="duka-item">${rendered.item || rendered.html}</div>`;
          },
          no_results: () => '<div class="no-results">No matches</div>',
        },
      });
    });
  }

  function sync(root = document) {
    root.querySelectorAll("select").forEach((select) => {
      if (select.tomselect) select.tomselect.sync();
    });
  }

  window.DukaSelect = { enhance, sync };
  enhance();
  // form.reset() changes the hidden <select>s; bring the styled controls along.
  document.addEventListener("reset", (event) => {
    window.setTimeout(() => sync(event.target), 0);
  }, true);
})();
