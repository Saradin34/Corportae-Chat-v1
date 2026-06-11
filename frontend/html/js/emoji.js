/* ============================================================
   Local emoji rendering — NO external requests (offline-safe).
   Wraps emoji characters in <span class="emoji"> so CSS controls
   the size, using the OS native emoji font (Segoe UI Emoji on
   Windows, Apple Color Emoji on macOS, Noto on Linux).

   Why local: any request to an external CDN (jsdelivr etc.) is
   flagged by corporate AV (Dr.Web) and breaks in closed networks.
   This module makes ZERO network calls.
   ============================================================ */
(function () {
  "use strict";

  // Matches emoji pictographic sequences (incl. ZWJ joins, skin tones,
  // variation selectors). Falls back gracefully if the engine is old.
  var EMOJI_RE = null;
  try {
    EMOJI_RE = new RegExp(
      "(\\p{Extended_Pictographic}(\\u200d\\p{Extended_Pictographic}|[\\uFE0F\\u{1F3FB}-\\u{1F3FF}])*)",
      "gu"
    );
  } catch (e) {
    EMOJI_RE = null; // very old browser: leave native glyphs as-is
  }

  // Wrap emoji in already-rendered DOM. Safe to call repeatedly.
  function parse(root) {
    if (!root || !EMOJI_RE) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        // skip inside elements where we shouldn't touch text
        var p = node.parentNode;
        if (!p) return NodeFilter.FILTER_REJECT;
        var tag = p.nodeName;
        if (tag === "SCRIPT" || tag === "STYLE" || tag === "TEXTAREA" || tag === "INPUT") {
          return NodeFilter.FILTER_REJECT;
        }
        if (p.classList && p.classList.contains("emoji")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });

    var targets = [];
    var n;
    while ((n = walker.nextNode())) {
      if (n.nodeValue && reHasEmoji(n.nodeValue)) targets.push(n);
    }

    targets.forEach(function (node) {
      var frag = document.createDocumentFragment();
      var text = node.nodeValue;
      var last = 0;
      EMOJI_RE.lastIndex = 0;
      var m;
      while ((m = EMOJI_RE.exec(text)) !== null) {
        if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        var span = document.createElement("span");
        span.className = "emoji";
        span.textContent = m[0];
        frag.appendChild(span);
        last = m.index + m[0].length;
      }
      if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
      if (node.parentNode) node.parentNode.replaceChild(frag, node);
    });
  }

  function reHasEmoji(s) {
    if (!EMOJI_RE) return false;
    EMOJI_RE.lastIndex = 0;
    return EMOJI_RE.test(s);
  }

  window.Emoji = {
    parse: parse,
    preload: function () {},      // no-op (nothing to load)
    isReady: function () { return true; },
  };
})();
