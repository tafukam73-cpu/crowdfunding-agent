"""HTML を読みやすいプレーンテキストへ変換するユーティリティ。

スクレイパーが取得した description には `<p>` `<figure>` `<img>` `<ul>` `<li>`
`<strong>` 等の HTML タグや画像 URL / alt 文字列がそのまま混ざることがある。
これらを取り除き、本文テキストだけを抽出する。長すぎる場合はトリムする。
"""
from __future__ import annotations

import html as _html
import re

from selectolax.parser import HTMLParser

# テキスト抽出時に内容ごと丸ごと除去するタグ（画像・装飾・スクリプト等）。
# これにより画像 URL・alt 文字列・キャプションの混入を防ぐ。
_DROP_TAGS = "script, style, noscript, template, img, picture, figure, svg, video, iframe"

# ブロック要素の境界は改行に変換する（インライン要素はテキストを連結する）。
_BLOCK_TAGS = (
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article",
    "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre",
)
_BLOCK_RE = re.compile(
    r"</?(?:" + "|".join(_BLOCK_TAGS) + r")\b[^>]*>", re.IGNORECASE
)
_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WS = re.compile(r"[ \t ​]+")
_MULTI_NL = re.compile(r"\n{3,}")


def html_to_text(value: str | None, *, max_len: int | None = None) -> str | None:
    """HTML 文字列から本文テキストだけを抽出して返す。

    - `<img>` / `<figure>` 等は内容ごと除去（画像 URL・alt の混入を防ぐ）。
    - ブロック要素（`<p>` `<li>` `<br>` 等）の境界だけ改行にし、インライン要素
      （`<strong>` 等）はテキストを連結する。
    - HTML エンティティ（`&amp;` 等）はデコードする。
    - `max_len` 指定時は語境界に近い位置でトリムし、末尾に「…」を付ける。

    HTML を含まない素のテキストは軽量に処理する。None / 空文字はそのまま返す。
    """
    if not value:
        return value

    if "<" not in value and "&" not in value:
        text = value
    else:
        # 画像・スクリプト等を内容ごと除去してから、ブロック境界を改行に変換する。
        tree = HTMLParser(value)
        for node in tree.css(_DROP_TAGS):
            node.decompose()
        root = tree.body or tree.root
        markup = root.html if root is not None else value
        markup = _BLOCK_RE.sub("\n", markup or "")
        markup = _TAG_RE.sub("", markup)
        text = _html.unescape(markup)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 行ごとに前後空白を整え、空行は落とす（段落区切りは最大 1 行残す）
    lines = [_INLINE_WS.sub(" ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(ln for ln in lines if ln)
    text = _MULTI_NL.sub("\n\n", text).strip()

    if max_len and len(text) > max_len:
        cut = text[:max_len]
        sp = cut.rfind(" ")
        if sp > max_len * 0.6:
            cut = cut[:sp]
        text = cut.rstrip(" .,、。\n") + "…"

    return text or None
