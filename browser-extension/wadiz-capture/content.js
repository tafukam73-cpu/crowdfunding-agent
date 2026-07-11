// Wadiz ページ内で動く content script。
// popup からのメッセージで「表示中の公開 DOM を取得」または「もっと見る候補をハイライト」。
// Akamai 回避・自動クリックはしない。Cookie/localStorage/認証情報は取得しない。

(function () {
  "use strict";

  var MORE_LABELS = ["더보기", "전체보기", "상세보기", "내용 더보기", "펼쳐보기"];

  function textNodesSample(limit) {
    var out = [];
    try {
      var walker = document.createTreeWalker(
        document.body, NodeFilter.SHOW_TEXT, null
      );
      var n;
      while ((n = walker.nextNode()) && out.length < limit) {
        var t = (n.nodeValue || "").trim();
        if (t) out.push(t);
      }
    } catch (e) {}
    return out;
  }

  function collectMeta() {
    var meta = {};
    document.querySelectorAll("meta").forEach(function (m) {
      var k = m.getAttribute("property") || m.getAttribute("name");
      if (k) meta[k] = m.getAttribute("content") || "";
    });
    return meta;
  }

  function collectJsonLd() {
    var out = [];
    document
      .querySelectorAll('script[type="application/ld+json"]')
      .forEach(function (s) {
        var txt = (s.textContent || "").trim();
        if (txt) out.push(txt);
      });
    return out;
  }

  // 同一オリジンの iframe のみ本文/HTML を追加取得（cross-origin は触らない）。
  function collectSameOriginIframes() {
    var html = "";
    var text = "";
    document.querySelectorAll("iframe").forEach(function (f) {
      try {
        var doc = f.contentDocument; // cross-origin なら例外/ null
        if (doc && doc.body) {
          html += "\n" + doc.documentElement.outerHTML;
          text += "\n" + (doc.body.innerText || "");
        }
      } catch (e) {
        // cross-origin iframe：無理に取得しない
      }
    });
    return { html: html, text: text };
  }

  // 「もっと見る」候補ボタンとその表示テキスト、aria-expanded=false の有無。
  function moreButtons() {
    var found = [];
    var hasCollapsed = false;
    var cands = document.querySelectorAll(
      'button, a, [role="button"], span, div'
    );
    cands.forEach(function (el) {
      var t = (el.innerText || el.textContent || "").trim();
      if (!t || t.length > 20) return;
      if (MORE_LABELS.some(function (l) { return t.indexOf(l) >= 0; })) {
        var rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) found.push(el);
      }
      if (el.getAttribute && el.getAttribute("aria-expanded") === "false") {
        hasCollapsed = true;
      }
    });
    return { elements: found, hasCollapsed: hasCollapsed };
  }

  function capture() {
    var iframes = collectSameOriginIframes();
    var mailtos = [].slice
      .call(document.querySelectorAll('a[href^="mailto:"]'))
      .map(function (a) { return a.href; });
    var tels = [].slice
      .call(document.querySelectorAll('a[href^="tel:"]'))
      .map(function (a) { return a.href; });
    var links = [].slice
      .call(document.links)
      .map(function (a) { return a.href; })
      .filter(function (h) { return /^https?:/i.test(h); });

    var mb = moreButtons();
    var bodyText = document.body ? document.body.innerText || "" : "";

    return {
      source_url: location.href,
      title: document.title,
      text: bodyText + iframes.text,
      html: document.documentElement.outerHTML + iframes.html,
      links: links,
      mailtos: mailtos,
      tels: tels,
      meta: collectMeta(),
      json_ld: collectJsonLd(),
      text_nodes: textNodesSample(400),
      captured_at: new Date().toISOString(),
      capture_version: "1",
      // 展開判定用（popup 側で警告表示）
      _more_button_texts: mb.elements.map(function (e) {
        return (e.innerText || e.textContent || "").trim();
      }),
      _has_aria_collapsed: mb.hasCollapsed,
      _body_text_length: bodyText.length,
    };
  }

  function highlightMore() {
    var mb = moreButtons();
    mb.elements.forEach(function (el) {
      el.style.outline = "3px solid #f59e0b";
      el.style.outlineOffset = "2px";
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    return { count: mb.elements.length };
  }

  chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
    try {
      if (msg && msg.type === "capture") {
        sendResponse({ ok: true, data: capture() });
      } else if (msg && msg.type === "highlight") {
        sendResponse({ ok: true, data: highlightMore() });
      } else {
        sendResponse({ ok: false, error: "unknown message" });
      }
    } catch (e) {
      sendResponse({ ok: false, error: String(e) });
    }
    return true;
  });
})();
