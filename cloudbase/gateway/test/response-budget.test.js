"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const source = fs.readFileSync(path.join(__dirname, "..", "index.js"), "utf8");

test("gateway responses share the desktop client's eight MiB budget", () => {
  assert.match(source, /const MAX_RESPONSE_BYTES = 8 \* 1024 \* 1024/);
  assert.match(source, /if \(body\.length > MAX_RESPONSE_BYTES\)/);
  assert.match(source, /网关响应超过大小限制/);
});

test("release descriptions are budgeted after JSON escaping", () => {
  assert.match(
    source,
    /Buffer\.byteLength\(JSON\.stringify\(body\), "utf8"\) > MAX_RELEASE_BODY_BYTES/,
  );
  assert.match(source, /发布说明 JSON 编码后超过大小限制/);
});
