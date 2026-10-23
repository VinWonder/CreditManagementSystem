(() => {
  // Invite phone users to install Duka Credit to their home screen.
  //
  // Android browsers (Chrome, Edge, Samsung Internet) fire `beforeinstallprompt`;
  // we keep it and show our own banner whose Install button opens the real
  // prompt. iPhone Safari has no such event, so the banner shows the two
  // taps (Share -> Add to Home Screen) instead; Android without the event
  // gets Chrome's menu -> Install app. "Not now" hides it for two weeks;
  // once installed it never shows again.
  //
  // An Android "Add to Home screen" shortcut opens with Chrome's logo first.
  // When the app is opened from one, a one-off tip explains how to swap it
  // for the full app (which opens with the Duka Credit logo).
  const DISMISS_KEY = "duka-install-dismissed-at";
  const SHORTCUT_KEY = "duka-shortcut-tip-at";
  const DISMISS_DAYS = 14;
  const SHORTCUT_TIP_DAYS = 30;
  const SHOW_AFTER_MS = 3500;
  const TITLES = {
    prompt: "Install Duka Credit",
    ios: "Add Duka Credit to your Home Screen",
    android: "Install Duka Credit from Chrome",
    shortcut: "Seeing the Chrome logo when it opens?",
  };

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/service-worker.js").catch(() => {});
    });
  }

  const banner = document.getElementById("install-banner");
  const menuItems = document.querySelectorAll("[data-install-app]");
  if (!banner) return;

  const installed =
    window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  const ua = window.navigator.userAgent;
  const isIos = /iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1);
  const isAndroid = /android/i.test(ua);
  const isPhoneOrTablet = window.matchMedia("(pointer: coarse)").matches && window.matchMedia("(max-width: 1100px)").matches;
  let deferredPrompt = null;
  let dismissKey = DISMISS_KEY;

  function recently(key, days) {
    try {
      const at = Number(localStorage.getItem(key) || 0);
      return Date.now() - at < days * 24 * 60 * 60 * 1000;
    } catch (error) {
      return false;
    }
  }

  function show(variant) {
    banner.querySelectorAll("[data-install-variant]").forEach((el) => {
      el.hidden = !el.dataset.installVariant.split(" ").includes(variant);
    });
    banner.querySelector("#install-banner-title").textContent = TITLES[variant];
    dismissKey = variant === "shortcut" ? SHORTCUT_KEY : DISMISS_KEY;
    banner.hidden = false;
    document.body.classList.add("install-banner-open");
  }

  function hide(remember) {
    banner.hidden = true;
    document.body.classList.remove("install-banner-open");
    if (remember) {
      try { localStorage.setItem(dismissKey, String(Date.now())); } catch (error) { /* private mode */ }
    }
  }

  banner.querySelectorAll("[data-install-dismiss]").forEach((button) => {
    button.addEventListener("click", () => hide(true));
  });

  if (installed) {
    // Chrome reports the full app (a WebAPK) here; a plain shortcut is not listed.
    if (isAndroid && typeof navigator.getInstalledRelatedApps === "function" && !recently(SHORTCUT_KEY, SHORTCUT_TIP_DAYS)) {
      navigator.getInstalledRelatedApps()
        .then((apps) => {
          if (!apps.some((app) => app.platform === "webapp")) {
            window.setTimeout(() => show("shortcut"), SHOW_AFTER_MS);
          }
        })
        .catch(() => {});
    }
    return;
  }

  function offerInMenu() {
    menuItems.forEach((item) => { item.hidden = false; });
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    offerInMenu();
    if (isPhoneOrTablet && !recently(DISMISS_KEY, DISMISS_DAYS)) {
      window.setTimeout(() => { if (deferredPrompt) show("prompt"); }, SHOW_AFTER_MS);
    }
  });

  if (isIos) {
    offerInMenu();
    if (!recently(DISMISS_KEY, DISMISS_DAYS)) window.setTimeout(() => show("ios"), SHOW_AFTER_MS);
  } else if (isAndroid) {
    offerInMenu();
  }

  banner.querySelector("[data-install-accept]").addEventListener("click", async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    const choice = await deferredPrompt.userChoice;
    deferredPrompt = null;
    hide(choice.outcome !== "accepted");
  });
  menuItems.forEach((item) => {
    item.addEventListener("click", (event) => {
      event.preventDefault();
      show(deferredPrompt ? "prompt" : isIos ? "ios" : "android");
    });
  });

  window.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    hide(false);
    menuItems.forEach((item) => { item.hidden = true; });
  });
})();
