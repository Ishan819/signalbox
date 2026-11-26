// Pure matching logic shared by content.js and the node tests. No DOM, no chrome.* APIs.
//
// Gmail web rows carry the thread id two ways:
//   data-legacy-thread-id="18c2f0a1b2c3d4e5"        (hex, same as the Gmail API threadId)
//   data-thread-id="#thread-f:1784512345678901234"  (decimal, same as X-GM-THRID in an mbox)
// signalbox's thread_id is hex when it came from gmail-sync and decimal when it came from an
// mbox import, so everything is normalized to lower-case hex before comparing.
(function (root) {
  "use strict";

  const DEFAULT_SERVER = "http://127.0.0.1:8000";

  function decimalToHex(dec) {
    try {
      return BigInt(dec).toString(16);
    } catch (_) {
      return null;
    }
  }

  // Every lower-case hex key a signalbox id might be known by in Gmail's DOM.
  function idKeys(id) {
    if (id === null || id === undefined) return [];
    let s = String(id).trim().toLowerCase();
    if (!s) return [];
    if (s.startsWith("#thread-f:") || s.startsWith("thread-f:")) {
      const hex = decimalToHex(s.slice(s.indexOf(":") + 1));
      return hex ? [hex] : [];
    }
    const keys = [s];
    // A long all-digit id is an X-GM-THRID (decimal); Gmail API ids are 16 hex chars.
    if (/^\d{17,20}$/.test(s)) {
      const hex = decimalToHex(s);
      if (hex) keys.push(hex);
    }
    return keys;
  }

  // items: the /api/needs-you feed. Indexed by thread id and by message id; the highest
  // score wins when a thread has several highlighted messages.
  function buildIndex(items) {
    const index = new Map();
    for (const item of items || []) {
      for (const key of [...idKeys(item.thread_id), ...idKeys(item.id)]) {
        const prev = index.get(key);
        if (!prev || item.score > prev.score) index.set(key, item);
      }
    }
    return index;
  }

  // rowIds: whatever ids a Gmail row exposes (legacy hex thread id, thread-f id, last message id).
  function lookup(index, rowIds) {
    let best = null;
    for (const raw of rowIds || []) {
      for (const key of idKeys(raw)) {
        const hit = index.get(key);
        if (hit && (!best || hit.score > best.score)) best = hit;
      }
    }
    return best;
  }

  // The extension only ever talks to a signalbox server on this machine.
  function isLocalServer(url) {
    try {
      const u = new URL(url);
      return u.protocol === "http:" && (u.hostname === "127.0.0.1" || u.hostname === "localhost")
        && u.pathname.replace(/\/+$/, "") === "" && !u.search && !u.hash && !u.username;
    } catch (_) {
      return false;
    }
  }

  function tooltip(item) {
    const label = item.tier === "needs_you" ? "Needs you" : "Worth a look";
    return `Signal · ${label} (${item.score.toFixed(2)}) — ${item.why}`;
  }

  const api = { DEFAULT_SERVER, idKeys, buildIndex, lookup, isLocalServer, tooltip };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SignalMatch = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
