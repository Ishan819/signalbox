// Run with: node --test extension/tests/*.test.js   (Node's built-in runner, no npm install)
const test = require("node:test");
const assert = require("node:assert/strict");
const { idKeys, buildIndex, lookup, isLocalServer, tooltip } = require("../match.js");

const HEX = "18c2f0a1b2c3d4e5";
const DEC = BigInt("0x" + HEX).toString();

test("idKeys normalizes Gmail's two thread-id spellings to the same hex key", () => {
  assert.deepEqual(idKeys(HEX.toUpperCase()), [HEX]);
  assert.deepEqual(idKeys(`#thread-f:${DEC}`), [HEX]);
  assert.deepEqual(idKeys(`thread-f:${DEC}`), [HEX]);
  assert.deepEqual(idKeys(DEC), [DEC, HEX]); // X-GM-THRID from an mbox import
});

test("idKeys ignores empty and junk input", () => {
  assert.deepEqual(idKeys(null), []);
  assert.deepEqual(idKeys(""), []);
  assert.deepEqual(idKeys("#thread-f:not-a-number"), []);
});

test("a gmail-sync item (hex thread id) matches a Gmail row", () => {
  const index = buildIndex([{ id: "m1", thread_id: HEX, score: 0.8, tier: "needs_you", why: "x" }]);
  assert.equal(lookup(index, [`#thread-f:${DEC}`]).id, "m1");
  assert.equal(lookup(index, [HEX]).id, "m1");
});

test("an mbox item (decimal X-GM-THRID) matches a Gmail row's legacy hex id", () => {
  const index = buildIndex([{ id: "<abc@x>", thread_id: DEC, score: 0.6, tier: "needs_you", why: "x" }]);
  assert.equal(lookup(index, [HEX]).id, "<abc@x>");
});

test("the highest-scoring item wins for a thread", () => {
  const index = buildIndex([
    { id: "a", thread_id: HEX, score: 0.3, tier: "worth_a_look", why: "" },
    { id: "b", thread_id: HEX, score: 0.9, tier: "needs_you", why: "" },
  ]);
  assert.equal(lookup(index, [HEX]).id, "b");
});

test("rows with no match, or no ids, return null", () => {
  const index = buildIndex([{ id: "m1", thread_id: HEX, score: 0.8, tier: "needs_you", why: "" }]);
  assert.equal(lookup(index, ["ffff"]), null);
  assert.equal(lookup(index, []), null);
  assert.equal(lookup(buildIndex(undefined), [HEX]), null);
});

test("isLocalServer only accepts plain http on this machine", () => {
  assert.ok(isLocalServer("http://127.0.0.1:8000"));
  assert.ok(isLocalServer("http://localhost:9000/"));
  assert.ok(!isLocalServer("https://127.0.0.1:8000"));
  assert.ok(!isLocalServer("http://example.com:8000"));
  assert.ok(!isLocalServer("http://127.0.0.1.evil.com"));
  assert.ok(!isLocalServer("http://user@127.0.0.1:8000"));
  assert.ok(!isLocalServer("http://127.0.0.1:8000/api?x=1"));
  assert.ok(!isLocalServer("not a url"));
});

test("tooltip explains the tier, score and reason", () => {
  const t = tooltip({ tier: "needs_you", score: 0.734, why: "Priya · asks you a direct question" });
  assert.equal(t, "Signal · Needs you (0.73) — Priya · asks you a direct question");
});
