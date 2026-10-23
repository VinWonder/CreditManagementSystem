(() => {
  // Invite phone users to install Duka Credit to their home screen.
  //
  // Android browsers (Chrome, Edge, Samsung Internet) fire `beforeinstallprompt`;
  // we keep it and show our own banner whose Install button opens the real
  // prompt. iPhone Safari has no such event, so the banner shows the two
  // taps (Share -> Add to Home Screen) instead. "Not now" hides it for two
  // weeks; once installed it never shows again.
  const DISMISS_KEY = "duka-install-dismissed-at";
  const DISMISS_DAYS = 14;
  const SHOW_AFTER_MS = 3500;

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/service-worker.js").catch(() => {});
    });
  }

  const installed =
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  const banner = document.getElementById("install-banner");
  const menuItems = document.querySelectorAll("[data-install-app]");
  if (installed || !banner) return;

  const ua = window.navigator.userAgent;
  const isIos = /iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1);
  const isPhoneOrTablet = window.matchMedia("(pointer: coarse)").matches && window.matchMedia("(max-width: 1100px)").matches;
  let deferredPrompt = null;

  function recentlyDismissed() {
    try {
      const at = Number(localStorage.getItem(DISMISS_KEY) || 0);
      return Date.now() - at < DISMISS_DAYS * 24 * 60 * 60 * 1000;
    } catch (error) {
      return false;
    }
  }

  function show(variant) {
    banner.querySelectorAll("[data-install-variant]").forEach((el) => {
      el.hidden = el.dataset.installVariant !== variant;
    });
    banner.querySelector("#install-banner-title").textContent =
      variant === "ios" ? "Add Duka Credit to your Home Screen" : "Install Duka Credit";
    banner.hidden = false;
    document.body.classList.add("install-banner-open");
  }

  function hide(remember) {
    banner.hidden = true;
    document.body.classList.remove("install-banner-open");
    if (remember) {
      try { localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch (error) { /* private mode */ }
    }
  }

  function offerInMenu() {
    menuItems.forEach((item) => { item.hidden = false; });
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    offerInMenu();
    if (isPhoneOrTablet && !recentlyDismissed()) {
      window.setTimeout(() => { if (deferredPrompt) show("prompt"); }, SHOW_AFTER_MS);
    }
  });

  if (isIos) {
    offerInMenu();
    if (!recentlyDismissed()) window.setTimeout(() => show("ios"), SHOW_AFTER_MS);
  }

  banner.querySelector("[data-install-accept]").addEventListener("click", async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const choice = await deferredPrompt.userChoice;
    deferredPrompt = null;
    hide(choice.outcome !== "accepted");
  });
  banner.querySelectorAll("[data-install-dismiss]").forEach((button) => {
    button.addEventListener("click", () => hide(true));
  });
  menuItems.forEach((item) => {
    item.addEventListener("click", (event) => {
      event.preventDefault();
      show(deferredPrompt ? "prompt" : "ios");
    });
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    hide(false);
    menuItems.forEach((item) => { item.hidden = true; });
  });
})();
