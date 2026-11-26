// Service worker: the only part of the extension that talks to the local signalbox server.
// A content script on mail.google.com can't fetch http://127.0.0.1 itself (mixed content +
// private network rules), but the extension can, through host_permissions.
importScripts("match.js");
const { DEFAULT_SERVER, isLocalServer } = self.SignalMatch;

async function fetchNeedsYou() {
  const { server } = await chrome.storage.sync.get("server");
  const base = isLocalServer(server) ? server.replace(/\/+$/, "") : DEFAULT_SERVER;
  try {
    const resp = await fetch(`${base}/api/needs-you`, { cache: "no-store" });
    if (!resp.ok) return { ok: false, error: `signalbox server returned HTTP ${resp.status}` };
    const data = await resp.json();
    return { ok: true, items: Array.isArray(data.items) ? data.items : [] };
  } catch (err) {
    return { ok: false, error: `can't reach signalbox at ${base} — is \`signalbox serve\` running?` };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !msg || msg.type !== "signal:needs-you") return false;
  fetchNeedsYou().then(sendResponse);
  return true; // keep the channel open for the async response
});
