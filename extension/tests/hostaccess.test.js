import { test } from "node:test";
import assert from "node:assert/strict";
import { originPatternFor } from "../lib/hostaccess.js";

test("originPatternFor maps a service URL to its origin pattern", () => {
  assert.equal(originPatternFor("https://api-service-abc123-uw.example.test"), "https://api-service-abc123-uw.example.test/*");
});

test("originPatternFor drops any path, query and trailing slash", () => {
  assert.equal(originPatternFor("https://example.test/v1/inbox?x=1/"), "https://example.test/*");
});

test("originPatternFor keeps a non-default port", () => {
  assert.equal(originPatternFor("https://example.test:8443"), "https://example.test:8443/*");
});

test("originPatternFor refuses http, empty, junk and credentialed URLs", () => {
  assert.throws(() => originPatternFor("http://example.test"), /must be https/);
  assert.throws(() => originPatternFor(""), /empty/);
  assert.throws(() => originPatternFor("not a url"), /not a valid URL/);
  assert.throws(() => originPatternFor("https://user:pw@example.test"), /credentials/);
});
