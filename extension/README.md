# Signal for Gmail (Chrome extension)

Marks the messages in your Gmail inbox that signalbox scored as **needs you** (orange stripe +
badge) or **worth a look** (amber), with the one-line reason as a tooltip.

All scoring happens in your local `signalbox serve`. The extension only fetches
`http://127.0.0.1:8000/api/needs-you` and adds classes to inbox rows. It doesn't read message
bodies in the page, send anything anywhere, or change your mail.

## Install (unpacked)

1. Keep a Signal JSON for your Gmail up to date. `gmail-sync` is best because its thread ids match
   Gmail's exactly:
   ```bash
   python -m signalbox gmail-sync --watch          # see the main README for gmail-auth setup
   python -m signalbox serve gmail_cache.json       # http://127.0.0.1:8000
   ```
   An mbox import (`import-mbox`) also works for Gmail Takeout files, which carry `X-GM-THRID`.
2. Open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and pick this
   `extension/` folder.
3. Open Gmail. Rows refresh every minute. If `serve` runs on another port, set it in the
   extension's **Options**. Only `127.0.0.1` and `localhost` addresses are accepted.

## How rows are matched

Gmail rows expose the thread id as `data-legacy-thread-id` (hex, the same as the Gmail API
`threadId`) and `data-thread-id="#thread-f:<decimal>"` (the same as `X-GM-THRID`). `match.js`
normalizes both to hex. That's the only logic with tests (`node --test extension/tests/*.test.js`).
The DOM side (`tr.zA` rows) depends on Gmail's markup, which Google changes without notice. If
highlights stop appearing, the selectors in `content.js` are the first thing to check.

## Permissions

| permission | why |
|---|---|
| `https://mail.google.com/*` (content script) | to add highlights to inbox rows |
| `http://127.0.0.1/*`, `http://localhost/*` | to read the feed from your local signalbox |
| `storage` | to remember the server address you set in Options |
