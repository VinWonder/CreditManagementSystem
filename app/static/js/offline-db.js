/**
 * offline-db.js
 * Thin wrapper around IndexedDB used to cache credit transactions recorded
 * while the retailer's device has no internet connection (common in
 * low-connectivity kiosk environments). Cached records are flushed to the
 * server by sync.js as soon as connectivity returns.
 */
const DukaOfflineDB = (() => {
  const DB_NAME = "duka_credit_offline";
  const DB_VERSION = 1;
  const STORE = "pending_transactions";

  function openDB() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, DB_VERSION);

      request.onupgradeneeded = (event) => {
        const db = event.target.result;
        if (!db.objectStoreNames.contains(STORE)) {
          const store = db.createObjectStore(STORE, { keyPath: "client_id" });
          store.createIndex("created_at", "created_at", { unique: false });
        }
      };

      request.onsuccess = (event) => resolve(event.target.result);
      request.onerror = (event) => reject(event.target.error);
    });
  }

  async function addPendingTransaction(txn) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      const store = tx.objectStore(STORE);
      const record = {
        client_id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        customer_id: txn.customer_id,
        item_description: txn.item_description,
        amount: txn.amount,
        created_at: new Date().toISOString(),
      };
      store.add(record);
      tx.oncomplete = () => resolve(record);
      tx.onerror = (e) => reject(e.target.error);
    });
  }

  async function getAllPending() {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readonly");
      const store = tx.objectStore(STORE);
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result);
      req.onerror = (e) => reject(e.target.error);
    });
  }

  async function removePending(client_id) {
    const db = await openDB();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).delete(client_id);
      tx.oncomplete = () => resolve();
      tx.onerror = (e) => reject(e.target.error);
    });
  }

  async function countPending() {
    const all = await getAllPending();
    return all.length;
  }

  return { addPendingTransaction, getAllPending, removePending, countPending };
})();
