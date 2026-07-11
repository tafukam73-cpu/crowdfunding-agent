// popup ロジック：取得 → project 特定 → preview → チェック選択 → confirm。
// 送信先は localhost のみ（WadizLib.isAllowedApiBase で検証）。
"use strict";

var state = { capture: null, projectId: null, preview: null };

function $(id) { return document.getElementById(id); }
function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
  return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
}); }
function setStatus(html, cls) {
  $("status").innerHTML = html ? '<div class="' + (cls || "") + '">' + html + "</div>" : "";
}
function apiBase() { return $("apiBase").value.trim().replace(/\/+$/, ""); }

async function activeTab() {
  var tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  return tabs[0];
}

function sendToTab(tabId, msg) {
  return new Promise(function (resolve) {
    chrome.tabs.sendMessage(tabId, msg, function (resp) {
      if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
      else resolve(resp || { ok: false, error: "no response" });
    });
  });
}

async function api(path, method, body) {
  var base = apiBase();
  if (!WadizLib.isAllowedApiBase(base)) {
    throw new Error("送信先が localhost ではありません（安全のため送信しません）: " + base);
  }
  var res = await fetch(base + path, {
    method: method || "GET",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error("API " + res.status + ": " + (await res.text()).slice(0, 200));
  return res.json();
}

$("highlight").addEventListener("click", async function () {
  var tab = await activeTab();
  var r = await sendToTab(tab.id, { type: "highlight" });
  if (r.ok) setStatus("「もっと見る」候補を " + r.data.count + " 件ハイライトしました。展開してから取得してください。", "warn");
  else setStatus("ハイライトに失敗（Wadizページで実行してください）", "err");
});

$("capture").addEventListener("click", async function () {
  $("result").innerHTML = "";
  $("project").innerHTML = "";
  setStatus("取得中…");
  try {
    var tab = await activeTab();
    if (!WadizLib.isWadizCampaignUrl(tab.url || "")) {
      setStatus("Wadiz商品ページ（/campaign/detail/...）で実行してください。", "err");
      return;
    }
    var cap = await sendToTab(tab.id, { type: "capture" });
    if (!cap.ok) { setStatus("取得失敗: " + esc(cap.error), "err"); return; }
    state.capture = cap.data;

    // 未展開警告
    if (WadizLib.needsExpansionWarning({
      buttonTexts: cap.data._more_button_texts,
      hasAriaCollapsed: cap.data._has_aria_collapsed,
      bodyTextLength: cap.data._body_text_length,
    })) {
      setStatus("「もっと見る」がまだ開かれていない可能性があります。ページ上で展開してから再取得してください。", "warn");
    } else {
      setStatus("");
    }

    // project 特定
    var rez = await api("/wadiz-browser-capture/resolve?url=" + encodeURIComponent(cap.data.source_url));
    renderProject(rez);
  } catch (e) {
    setStatus("エラー: " + esc(e.message), "err");
  }
});

function renderProject(rez) {
  var html = '<div class="box"><b>対象プロジェクト</b> ';
  html += '<span class="small">campaign ' + esc(rez.campaign_id || "-") + "</span><br/>";
  if (rez.match === "unique") {
    state.projectId = rez.candidates[0].project_id;
    var c = rez.candidates[0];
    html += "✅ #" + c.project_id + " " + esc((c.title || "").slice(0, 40));
  } else if (rez.match === "multiple") {
    html += '<span class="warn">複数候補あり。選択してください（自動確定しません）：</span><br/>';
    html += '<select id="psel">';
    rez.candidates.forEach(function (c) {
      html += '<option value="' + c.project_id + '">#' + c.project_id + " " + esc((c.title || "").slice(0, 30)) + "</option>";
    });
    html += "</select>";
  } else {
    html += '<span class="err">一致するプロジェクトがありません。project ID を入力してください：</span> ';
    html += '<input type="text" id="pidInput" style="width:80px" placeholder="project id" />';
  }
  html += ' <button id="doPreview" class="primary">抽出プレビュー</button></div>';
  $("project").innerHTML = html;
  $("doPreview").addEventListener("click", doPreview);
}

async function doPreview() {
  try {
    if ($("psel")) state.projectId = Number($("psel").value);
    if ($("pidInput")) state.projectId = Number($("pidInput").value);
    if (!state.projectId) { setStatus("プロジェクトを指定してください。", "err"); return; }
    setStatus("抽出中…");
    var body = {
      source_url: state.capture.source_url,
      title: state.capture.title,
      text: state.capture.text,
      html: state.capture.html,
      links: state.capture.links,
      mailtos: state.capture.mailtos,
      tels: state.capture.tels,
      meta: state.capture.meta,
      json_ld: state.capture.json_ld,
      captured_at: state.capture.captured_at,
      capture_version: state.capture.capture_version,
    };
    var pv = await api("/projects/" + state.projectId + "/wadiz-browser-capture/preview", "POST", body);
    state.preview = pv;
    setStatus("");
    renderPreview(pv);
  } catch (e) {
    setStatus("エラー: " + esc(e.message), "err");
  }
}

function tag(cls, label) { return '<span class="tag ' + cls + '">' + label + "</span>"; }

function renderPreview(pv) {
  var h = '<div class="box">';
  if (pv.already_imported) h += '<div class="warn">この内容は取り込み済みです（保存すると重複判定）。</div>';
  (pv.warnings || []).forEach(function (w) { h += '<div class="warn">⚠ ' + esc(w) + "</div>"; });

  h += "<b>抽出メール（チェックしたものだけ保存）</b>";
  if (!pv.emails.length) h += '<div class="small">公開メールは抽出されませんでした。</div>';
  pv.emails.forEach(function (e, i) {
    h += '<div class="email"><input type="checkbox" class="em" data-i="' + i + '" ' +
      (e.is_new !== false && e.region !== "chrome" ? "checked" : "") + "/>";
    h += '<span><span class="mono">' + esc(e.value) + "</span> ";
    h += tag(e.confidence || "medium", e.confidence || "medium") + " ";
    if (e.region === "chrome") h += tag("chrome", "footer/nav") + " ";
    if (e.is_new === false) h += tag("chrome", "既存") + " "; else h += tag("new", "新規") + " ";
    h += '<div class="small">' + esc(e.extraction_method) + " / " + esc(e.evidence) + "</div></span></div>";
  });

  if ((pv.excluded || []).length) {
    h += "<b>除外されたメール（理由）</b>";
    pv.excluded.forEach(function (x) {
      h += '<div class="small mono">' + esc(x.value) + " — " + esc(x.reason) + "</div>";
    });
  }

  h += "<div><b>公式サイト候補:</b> " + ((pv.official_urls || []).map(esc).join(", ") || "-") + "</div>";
  h += "<div><b>問い合わせURL:</b> " + ((pv.contact_urls || []).map(esc).join(", ") || "-") + "</div>";
  h += "<div><b>SNS:</b> " + (Object.keys(pv.socials || {}).join(", ") || "-") + "</div>";
  h += "<div><b>メーカー名候補:</b> " + esc(pv.maker_name || "-") + "</div>";
  h += "<div><b>電話:</b> " + ((pv.phones || []).map(esc).join(", ") || "-") + "</div>";
  h += '<div class="row"><button id="save" class="save">確認して保存</button>';
  h += '<span class="small">差分: 新規 ' + (pv.new_email_count || 0) + " / 既存 " + (pv.already_have_count || 0) + "</span></div>";
  h += "</div>";
  $("result").innerHTML = h;
  $("save").addEventListener("click", doConfirm);
}

async function doConfirm() {
  var btn = $("save");
  if (btn && btn.disabled) return;  // 二重クリック防止
  if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
  try {
    var picked = [].slice.call(document.querySelectorAll(".em:checked")).map(function (cb) {
      return state.preview.emails[Number(cb.getAttribute("data-i"))];
    });
    if (!picked.length && !state.preview.official_url) {
      setStatus("保存する項目を選択してください。", "err");
      if (btn) { btn.disabled = false; btn.textContent = "確認して保存"; }
      return;
    }
    setStatus("保存中…");
    // 冪等：同一 content_hash は再送しても重複保存されない（サーバ側で判定）。
    var r = await api("/projects/" + state.projectId + "/wadiz-browser-capture/confirm", "POST", {
      content_hash: state.preview.content_hash,
      emails: picked,
      socials: state.preview.socials,
      official_url: state.preview.official_url,
      maker_name: state.preview.maker_name,
      source_url: state.capture.source_url,
    });
    if (r.already_imported) {
      setStatus("同一内容は取り込み済みでした（重複保存なし）。", "ok");
      if (btn) btn.textContent = "保存済み";
      return;
    }
    // 保存はここで完了。再評価は待たずにバックグラウンドで進む。
    var base = "保存しました：メール " + r.saved_emails + " 件" +
      (r.contact_found ? "・Contact Intelligence 反映" : "");
    if (btn) btn.textContent = "保存済み";
    if (r.reassessment_job_id) {
      setStatus(base + "<br/><span class=\"small\">Sales Copilot を再評価中…（画面は待たずに閉じて構いません）</span>", "ok");
      pollReassessment(r.reassessment_job_id, base);
    } else {
      setStatus(base, "ok");
    }
  } catch (e) {
    setStatus("保存エラー: " + esc(e.message), "err");
    if (btn) { btn.disabled = false; btn.textContent = "確認して保存"; }
  }
}

// 再評価ジョブを別APIでポーリング（3.5秒間隔・最大 ~1 分）。confirm 応答は待たない。
function pollReassessment(jobId, base) {
  var tries = 0;
  var MAX = 18;  // 18 * 3.5s ≒ 63s で打ち切り
  var timer = setInterval(async function () {
    tries++;
    try {
      var j = await api("/contact-intelligence/jobs/" + jobId);
      if (j.status === "completed") {
        clearInterval(timer);
        setStatus(base + "・Sales Copilot 再評価 完了", "ok");
      } else if (j.status === "failed" || j.status === "cancelled") {
        clearInterval(timer);
        // 再評価が失敗しても保存済みメールは消えない。
        setStatus(base + "<br/><span class=\"small\">（Sales Copilot 再評価は後で自動再試行されます）</span>", "ok");
      } else if (tries >= MAX) {
        clearInterval(timer);
        setStatus(base + "<br/><span class=\"small\">（Sales Copilot 再評価は進行中。案件詳細で確認できます）</span>", "ok");
      }
    } catch (e) {
      if (tries >= MAX) { clearInterval(timer); }
    }
  }, 3500);
}
