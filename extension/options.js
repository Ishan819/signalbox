(function () {
  "use strict";
  const { DEFAULT_SERVER, isLocalServer } = globalThis.SignalMatch;
  const input = document.getElementById("server");
  const status = document.getElementById("status");

  function show(text, cls) {
    status.textContent = text;
    status.className = cls;
  }

  chrome.storage.sync.get("server").then(({ server }) => {
    input.value = server || DEFAULT_SERVER;
  });

  document.getElementById("save").addEventListener("click", async () => {
    const value = input.value.trim().replace(/\/+$/, "") || DEFAULT_SERVER;
    if (!isLocalServer(value)) {
      show("Use an http://127.0.0.1:<port> or http://localhost:<port> address.", "err");
      return;
    }
    await chrome.storage.sync.set({ server: value });
    try {
      const resp = await fetch(`${value}/api/needs-you`, { cache: "no-store" });
      const data = await resp.json();
      show(`Saved. Connected — ${data.items.length} message(s) to highlight.`, "ok");
    } catch (_) {
      show("Saved, but couldn't reach the server. Is `signalbox serve` running?", "err");
    }
  });
})();
