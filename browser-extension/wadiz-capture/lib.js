// Wadiz Capture 拡張の純粋関数（DOM 非依存・node でテスト可能）。
// URL 正規化 / campaign ID 抽出 / 送信先制限 / 未展開警告判定。

(function (root) {
  "use strict";

  // 送信を許可するのは localhost のみ（安全条件）。
  function isAllowedApiBase(url) {
    if (typeof url !== "string") return false;
    return (
      /^https?:\/\/localhost(:\d+)?(\/|$)/i.test(url) ||
      /^https?:\/\/127\.0\.0\.1(:\d+)?(\/|$)/i.test(url)
    );
  }

  // Wadiz URL を正規化（クエリ/ハッシュ/末尾スラッシュ除去・小文字ホスト）。
  function normalizeUrl(url) {
    if (typeof url !== "string") return "";
    var u = url.trim().split("#")[0].split("?")[0].replace(/\/+$/, "");
    return u;
  }

  // /campaign/detail/{id} または /campaign/example/{id} から ID を取り出す。
  function extractCampaignId(url) {
    if (typeof url !== "string") return null;
    var m = url.match(/\/campaign\/(?:detail|example)\/([A-Za-z0-9_\-]+)/);
    return m ? m[1] : null;
  }

  // Wadiz 商品ページ URL か。
  function isWadizCampaignUrl(url) {
    return (
      typeof url === "string" &&
      /(^|\.)wadiz\.kr\//.test(url) &&
      extractCampaignId(url) !== null
    );
  }

  // 「もっと見る」未展開の可能性を判定する。
  //   opts: { text, buttonTexts:[], hasAriaCollapsed:bool, bodyTextLength:int }
  var MORE_LABELS = ["더보기", "전체보기", "상세보기", "내용 더보기", "펼쳐보기"];
  function needsExpansionWarning(opts) {
    opts = opts || {};
    var buttons = opts.buttonTexts || [];
    var visibleMore = buttons.some(function (t) {
      return MORE_LABELS.some(function (l) {
        return (t || "").indexOf(l) >= 0;
      });
    });
    // 展開ボタンが見えている / aria-expanded=false が残っている / 本文が極端に短い
    if (visibleMore) return true;
    if (opts.hasAriaCollapsed) return true;
    if (typeof opts.bodyTextLength === "number" && opts.bodyTextLength < 800) {
      return true;
    }
    return false;
  }

  var api = {
    isAllowedApiBase: isAllowedApiBase,
    normalizeUrl: normalizeUrl,
    extractCampaignId: extractCampaignId,
    isWadizCampaignUrl: isWadizCampaignUrl,
    needsExpansionWarning: needsExpansionWarning,
    MORE_LABELS: MORE_LABELS,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.WadizLib = api;
})(typeof window !== "undefined" ? window : globalThis);
