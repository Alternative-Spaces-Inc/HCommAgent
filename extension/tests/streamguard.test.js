// HCA-P20 Day 5: the poll alarm must not open a second stream while one is
// live, and a stream.closed frame ends the read loop without reopening.
// background.js runs only inside Chrome, so this test reads its source.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { parseSSEChunk } from "../lib/api.js";

const src = readFileSync(new URL("../background.js", import.meta.url), "utf8");

test("the poll alarm reconnects the stream only when none is active", () => {
  const alarm = src.slice(src.indexOf("chrome.alarms.onAlarm.addListener"));
  const body = alarm.slice(0, alarm.indexOf("});") + 3);
  assert.match(body, /if \(!streamActive\) connectStream\(\);/);
  assert.doesNotMatch(body, /^\s*connectStream\(\);/m);
});

test("connectStream marks the stream active while reading and clears it in finally", () => {
  const fn = src.slice(src.indexOf("async function connectStream()"));
  const body = fn.slice(0, fn.indexOf("\n}\n") + 3);
  assert.match(body, /streamActive = true;/);
  assert.match(body, /finally \{\s*streamActive = false;/);
  assert.match(body, /evt\.event === "stream\.closed"/);
});

test("a stream.closed frame parses with its reason", () => {
  const { events } = parseSSEChunk("", 'event: stream.closed\ndata: {"event": "stream.closed", "data": {"reason": "superseded"}, "id": null}\n\n');
  assert.equal(events.length, 1);
  assert.equal(events[0].event, "stream.closed");
  assert.equal(events[0].data.data.reason, "superseded");
});
