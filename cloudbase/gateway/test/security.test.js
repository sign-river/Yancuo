"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  boundedName,
  boundedText,
  newUploadToken,
  safeTokenEqual,
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
