// Runs on mail.google.com: marks inbox rows that signalbox scored as needs-you / worth-a-look.
// Read-only: it never clicks, moves, or reads message bodies — it only adds a class, a badge,
// and a tooltip to rows whose thread id is in the local server's feed.
(function () {
  "use strict";
  const { buildIndex, lookup, tooltip } = globalThis.SignalMatch;
  const REFRESH_MS = 60 * 1000;
  const CLASSES = { needs_you: "signal-needs-you", worth_a_look: "signal-worth-a-look" };

  let index = new Map();
  let scheduled = false;

  function rowIds(row) {
    const ids = [];
    for (const el of row.querySelectorAll("[data-legacy-thread-id], [data-thread-id], [data-legacy-last-message-id]")) {
      for (const attr of ["data-legacy-thread-id", "data-thread-id", "data-legacy-last-message-id"]) {
        const v = el.getAttribute(attr);
        if (v) ids.push(v);
      }
    }
    return ids;
  }

  function clear(row) {
    row.classList.remove(...Object.values(CLASSES));
    row.querySelectorAll(".signal-badge").forEach((b) => b.remove());
    if (row.dataset.signalTitle) {
      row.removeAttribute("title");
      delete row.dataset.signalTitle;
    }
  }

  function decorate() {
    scheduled = false;
    // tr.zA is Gmail's thread-list row. If Gmail renames it this quietly does nothing.
    for (const row of document.querySelectorAll("tr.zA")) {
      const hit = lookup(index, rowIds(row));
      const wanted = hit ? CLASSES[hit.tier] : null;
      if (wanted && row.classList.contains(wanted) && row.querySelector(".signal-badge")) continue;
      clear(row);
      if (!wanted) continue;
      row.classList.add(wanted);
      row.title = tooltip(hit);
      row.dataset.signalTitle = "1";
      const subject = row.querySelector(".y6, .bog");
      if (subject) {
        const badge = document.createElement("span");
        badge.className = "signal-badge";
        badge.textContent = hit.tier === "needs_you" ? "needs you" : "look";
        subject.prepend(badge);
      }
    }
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(decorate);
  }

  function refresh() {
    chrome.runtime.sendMessage({ type: "signal:needs-you" }, (resp) => {
      if (chrome.runtime.lastError || !resp) return;
      if (!resp.ok) {
        console.debug("[Signal]", resp.error);
        return;
      }
      index = buildIndex(resp.items);
      schedule();
    });
  }

  new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
