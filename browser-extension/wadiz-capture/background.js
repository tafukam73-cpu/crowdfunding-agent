// MV3 サービスワーカー（最小構成）。
// 送信・抽出は popup が localhost に対して行うため、ここでは何も常駐させない。
// Cookie / 認証情報 / セッションには一切アクセスしない。
chrome.runtime.onInstalled.addListener(function () {
  console.log("Wadiz Contact Capture installed (localhost-only, no bypass).");
});
