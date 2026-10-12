import { test } from "node:test";
import assert from "node:assert/strict";
import { fetchInbox, buildAnswerPayload, parseSSEChunk, unwrapStreamEnvelope, isAddressedTo, priorityLabel, PRIORITY_CHOICES, SURFACE, SUPPORTED_CONTROLS } from "../lib/api.js";

test("buildAnswerPayload for radio uses a single option id", () => {
  const body = buildAnswerPayload({ control: "radio", selection: "opt_a" });
  assert.deepEqual(body, { control: "radio", value: "opt_a", surface: SURFACE });
});

test("buildAnswerPayload for approve_deny uses a single option id", () => {
  const body = buildAnswerPayload({ control: "approve_deny", selection: "approve" });
  assert.deepEqual(body, { control: "approve_deny", value: "approve", surface: SURFACE });
});

test("buildAnswerPayload for checkbox uses an array of option ids", () => {
  const body = buildAnswerPayload({ control: "checkbox", selection: ["opt_a", "opt_c"] });
  assert.deepEqual(body, { control: "checkbox", value: ["opt_a", "opt_c"], surface: SURFACE });
});

test("buildAnswerPayload includes answered_by and device when given", () => {
  const body = buildAnswerPayload({ control: "radio", selection: "opt_a", answeredBy: "al", deviceId: "dev-1" });
  assert.deepEqual(body, { control: "radio", value: "opt_a", surface: SURFACE, answered_by: "al", device: "dev-1" });
});

test("buildAnswerPayload rejects a control this client does not support", () => {
  assert.throws(() => buildAnswerPayload({ control: "unknown", selection: "hello" }), /unsupported control/);
});

test("buildAnswerPayload rejects a non-array checkbox selection", () => {
  assert.throws(() => buildAnswerPayload({ control: "checkbox", selection: "opt_a" }), /array of distinct option ids/);
});

test("buildAnswerPayload rejects an empty radio selection", () => {
  assert.throws(() => buildAnswerPayload({ control: "radio", selection: "" }), /single option id/);
});

test("SUPPORTED_CONTROLS matches every v1 API control", () => {
  assert.deepEqual([...SUPPORTED_CONTROLS].sort(), ["approve_deny", "chat", "checkbox", "confirm_rows", "dropdown", "radio"]);
});

test("parseSSEChunk ignores the connected comment and parses no events", () => {
  const { events, buffer } = parseSSEChunk("", ": connected\n\n");
  assert.deepEqual(events, []);
  assert.equal(buffer, "");
});

test("parseSSEChunk parses one full event block", () => {
  const chunk = 'event: request.created\ndata: {"event":"request.created","data":{"request_id":"r1","title":"pick one"},"timestamp":"2026-09-30T12:00:00+00:00"}\n\n';
  const { events, buffer } = parseSSEChunk("", chunk);
  assert.equal(events.length, 1);
  assert.equal(events[0].event, "request.created");
  assert.deepEqual(events[0].data, { event: "request.created", data: { request_id: "r1", title: "pick one" }, timestamp: "2026-09-30T12:00:00+00:00" });
  assert.deepEqual(unwrapStreamEnvelope(events[0].data), { request_id: "r1", title: "pick one" });
  assert.equal(buffer, "");
});

test("parseSSEChunk carries the id line (HCA-P20 Last-Event-ID) and null without it", () => {
  const withId = 'id: 57\nevent: request.cancelled\ndata: {"id":57,"event":"request.cancelled","data":{"request_id":"r9"},"timestamp":"2026-10-10T19:00:00+00:00"}\n\n';
  const { events } = parseSSEChunk("", withId);
  assert.equal(events.length, 1);
  assert.equal(events[0].id, "57");
  const legacy = parseSSEChunk("", 'event: request.cancelled\ndata: {"event":"request.cancelled","data":{"request_id":"r9"}}\n\n');
  assert.equal(legacy.events[0].id, null);
});

test("parseSSEChunk buffers a partial block across two chunks", () => {
  const first = 'event: request.answered\ndata: {"event":"request.answered","data":{"reque';
  const second = 'st_id":"r2"},"timestamp":"2026-09-30T12:00:00+00:00"}\n\n';
  const step1 = parseSSEChunk("", first);
  assert.deepEqual(step1.events, []);
  assert.equal(step1.buffer, first);

  const step2 = parseSSEChunk(step1.buffer, second);
  assert.equal(step2.events.length, 1);
  assert.equal(step2.events[0].event, "request.answered");
  assert.deepEqual(unwrapStreamEnvelope(step2.events[0].data), { request_id: "r2" });
  assert.equal(step2.buffer, "");
});

test("parseSSEChunk parses multiple events and a keep-alive in one chunk", () => {
  const chunk =
    ': keep-alive\n\n' +
    'event: request.cancelled\ndata: {"event":"request.cancelled","data":{"request_id":"r3"},"timestamp":"t"}\n\n' +
    'event: request.expired\ndata: {"event":"request.expired","data":{"request_id":"r4"},"timestamp":"t"}\n\n';
  const { events, buffer } = parseSSEChunk("", chunk);
  assert.equal(events.length, 2);
  assert.equal(events[0].event, "request.cancelled");
  assert.equal(events[1].event, "request.expired");
  assert.equal(buffer, "");
});

test("parseSSEChunk drops a malformed data line without throwing", () => {
  const chunk = 'event: request.created\ndata: {not json}\n\n';
  const { events } = parseSSEChunk("", chunk);
  assert.equal(events.length, 1);
  assert.equal(events[0].data, null);
  assert.ok(events[0].parseError);
});

test("unwrapStreamEnvelope pulls the record out from under the publish envelope's data key", () => {
  const envelope = { event: "request.created", data: { request_id: "r5" }, timestamp: "t" };
  assert.deepEqual(unwrapStreamEnvelope(envelope), { request_id: "r5" });
});

test("unwrapStreamEnvelope passes through a value that has no data key", () => {
  assert.deepEqual(unwrapStreamEnvelope({ request_id: "r6" }), { request_id: "r6" });
  assert.equal(unwrapStreamEnvelope(null), null);
});

test("isAddressedTo treats a record without visible_to as addressed to everyone (pre-P14 server)", () => {
  assert.equal(isAddressedTo({ request_id: "r1" }, "dev_a"), true);
  assert.equal(isAddressedTo({ request_id: "r1", visible_to: "junk" }, "dev_a"), true);
});

test("isAddressedTo honours visible_to for this device only", () => {
  const rec = { request_id: "r1", visible_to: ["dev_primary"] };
  assert.equal(isAddressedTo(rec, "dev_primary"), true);
  assert.equal(isAddressedTo(rec, "dev_secondary"), false);
  assert.equal(isAddressedTo(rec, null), false);
  assert.equal(isAddressedTo({ request_id: "r1", visible_to: [] }, "dev_primary"), false);
});

test("priorityLabel names the three ladder places and labels anything else custom", () => {
  assert.equal(PRIORITY_CHOICES.map((c) => c.value).join(","), "10,100,200");
  assert.match(priorityLabel(10), /^Primary/);
  assert.match(priorityLabel("100"), /^Default/);
  assert.match(priorityLabel(200), /^Fallback/);
  assert.equal(priorityLabel(37), "Custom (37)");
});


test("new controls serialize the server contract and reject invalid decisions", () => {
  assert.equal(buildAnswerPayload({ control: "dropdown", selection: "one" }).value, "one");
  assert.equal(buildAnswerPayload({ control: "chat", selection: "Explain this" }).value, "Explain this");
  assert.deepEqual(buildAnswerPayload({ control: "confirm_rows", selection: ["r1"] }).value, ["r1"]);
  assert.throws(() => buildAnswerPayload({ control: "chat", selection: " " }), /message/);
  assert.throws(() => buildAnswerPayload({ control: "chat", selection: "a".repeat(4001) }), /message/);
  assert.throws(() => buildAnswerPayload({ control: "confirm_rows", selection: [] }), /at least/);
  assert.throws(() => buildAnswerPayload({ control: "confirm_rows", selection: ["r1", "r1"] }), /distinct/);
});


test("inbox collects bounded signed pages and detects cursor loops", async () => {
  const urls = [];
  const rows = await fetchInbox("https://api.example", { fetchFn: async (url) => {
    urls.push(url); const second = urls.length === 2;
    return { ok: true, json: async () => [{ request_id: second ? "r2" : "r1" }], headers: new Headers(second ? {} : { "X-HCA-Next-Cursor": "r1/?" }) };
  } });
  assert.equal(rows.length, 2);
  assert.equal(urls[1], "https://api.example/v1/inbox?limit=200&after=r1%2F%3F");
  await assert.rejects(fetchInbox("https://api.example", { fetchFn: async () => ({ ok: true, json: async () => [], headers: new Headers({ "X-HCA-Next-Cursor": "same" }) }) }), /repeated cursor/);
});
