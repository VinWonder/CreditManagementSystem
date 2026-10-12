/**
 * sync.js
 * Watches browser connectivity. When online, flushes any credit
 * transactions cached in IndexedDB (recorded while offline) to the server
 * via /transactions/api/sync, then updates the UI badge.
 */
(function () {
  const badge = document.getElementById("offline-badge");

  function updateBadge() {
    if (!badge) return;
    if (navigator.onLine) {
      badge.classList.add("d-none");
    } else {
      badge.classList.remove("d-none");
      badge.textContent = "Offline mode";
    }
  }

  async function syncPendingTransactions() {
    if (!navigator.onLine) return;
    const pending = await DukaOfflineDB.getAllPending();
    if (pending.length === 0) return;

    if (badge) {
      badge.classList.remove("d-none");
      badge.classList.remove("text-bg-warning");
      badge.classList.add("text-bg-info");
      badge.textContent = `Syncing ${pending.length}...`;
    }

    try {
      const resp = await fetch("/transactions/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transactions: pending }),
      });
      const data = await resp.json();
      for (const result of data.results) {
        if (result.status === "synced") {
          await DukaOfflineDB.removePending(result.client_id);
        }
      }
    } catch (err) {
      console.warn("Sync failed, will retry later:", err);
    } finally {
      if (badge) {
        badge.classList.remove("text-bg-info");
        badge.classList.add("text-bg-warning");
      }
      updateBadge();
    }
  }

  window.addEventListener("online", () => {
    updateBadge();
    syncPendingTransactions();
  });
  window.addEventListener("offline", updateBadge);
  document.addEventListener("DOMContentLoaded", () => {
    updateBadge();
    syncPendingTransactions();
  });

  // Expose globally so the "record credit sale" form can use it directly.
  window.DukaSync = { syncPendingTransactions };
})();
