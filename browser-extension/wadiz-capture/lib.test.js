// lib.js の純粋関数テスト（フレームワーク不要： node lib.test.js で実行）。
"use strict";
var L = require("./lib.js");
var assert = require("assert");

var passed = 0;
function t(name, fn) {
  try { fn(); passed++; console.log("  ok  - " + name); }
  catch (e) { console.log("  FAIL- " + name + " : " + e.message); process.exitCode = 1; }
}

t("localhost 送信のみ許可", function () {
  assert.strictEqual(L.isAllowedApiBase("http://localhost:8000"), true);
  assert.strictEqual(L.isAllowedApiBase("http://127.0.0.1:8000"), true);
  assert.strictEqual(L.isAllowedApiBase("https://www.wadiz.kr"), false);
  assert.strictEqual(L.isAllowedApiBase("http://evil.example.com"), false);
});

t("campaign ID 抽出", function () {
  assert.strictEqual(
    L.extractCampaignId("https://www.wadiz.kr/web/campaign/detail/406038"), "406038"
  );
  assert.strictEqual(
    L.extractCampaignId("https://www.wadiz.kr/web/campaign/example/hydroponics"),
    "hydroponics"
  );
  assert.strictEqual(L.extractCampaignId("https://www.wadiz.kr/"), null);
});

t("Wadiz 商品ページ判定", function () {
  assert.strictEqual(
    L.isWadizCampaignUrl("https://www.wadiz.kr/web/campaign/detail/406038"), true
  );
  assert.strictEqual(L.isWadizCampaignUrl("https://www.wadiz.kr/"), false);
  assert.strictEqual(L.isWadizCampaignUrl("https://example.com/campaign/detail/1"), false);
});

t("URL 正規化", function () {
  assert.strictEqual(
    L.normalizeUrl("https://www.wadiz.kr/web/campaign/detail/1/?x=1#a"),
    "https://www.wadiz.kr/web/campaign/detail/1"
  );
});

t("未展開警告：더보기 が見えていれば警告", function () {
  assert.strictEqual(
    L.needsExpansionWarning({ buttonTexts: ["더보기"], bodyTextLength: 5000 }), true
  );
});

t("未展開警告：aria-collapsed 残存で警告", function () {
  assert.strictEqual(
    L.needsExpansionWarning({ buttonTexts: [], hasAriaCollapsed: true, bodyTextLength: 5000 }),
    true
  );
});

t("未展開警告：本文が極端に短いと警告", function () {
  assert.strictEqual(L.needsExpansionWarning({ bodyTextLength: 100 }), true);
});

t("展開済み・十分な本文なら警告なし", function () {
  assert.strictEqual(
    L.needsExpansionWarning({ buttonTexts: ["접기"], hasAriaCollapsed: false, bodyTextLength: 5000 }),
    false
  );
});

console.log("\n" + passed + " passed");
