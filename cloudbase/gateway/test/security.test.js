"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  boundedName,
  boundedText,
  integerSetting,
  newUploadToken,
  safeTokenEqual,
  subjectStorageKey,
  tokenHash,
} = require("../security");

test("repository names are bounded", () => {
  assert.equal(boundedName("math-2026", "name"), "math-2026");
  assert.throws(() => boundedName("../escape", "name"));
  assert.throws(() => boundedName("x".repeat(81), "name"));
});

test("opaque upload tokens compare by hash", () => {
  const token = newUploadToken();
  assert.equal(safeTokenEqual(token, tokenHash(token)), true);
  assert.equal(safeTokenEqual(`${token}x`, tokenHash(token)), false);
});

test("text budgets are byte based", () => {
  assert.equal(boundedText("题目", "body", 6), "题目");
  assert.throws(() => boundedText("题目", "body", 5));
});

test("storage namespaces never expose raw identity subjects", () => {
  const subject = "user/name@example.com";
  const key = subjectStorageKey(subject);
  assert.match(key, /^[a-f0-9]{64}$/);
  assert.equal(key.includes(subject), false);
  assert.equal(subjectStorageKey(subject), key);
  assert.notEqual(subjectStorageKey("other-user"), key);
});

test("numeric deployment settings fail closed", () => {
  assert.equal(integerSetting(undefined, "LIMIT", 5, 1, 10), 5);
  assert.equal(integerSetting("10", "LIMIT", 5, 1, 10), 10);
  for (const value of ["NaN", "1.5", "0", "11", "Infinity"]) {
    assert.throws(() => integerSetting(value, "LIMIT", 5, 1, 10), /LIMIT/);
  }
});
