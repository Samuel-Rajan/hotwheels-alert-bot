"""
Stock verification, deliverability gate and add-to-cart for FirstCry.

* Verifier     : opens the product page and confirms an ADD TO CART button really exists.
* CartSession  : works through the Wishlist, WITHOUT opening the product page to add:
                   listing card -> Shortlist -> My Shortlist -> (read the car's row) -> Add to cart -> check cart
                 - gate(): shortlists the car and reads its row on My Shortlist. The row says ADD TO CART when the car
                           is buyable at the session's delivery pincode, or "Undeliverable / Out of stock" when not.
                 - add():  clicks the row's Add to cart, up to CART_ATTEMPTS times, until the car is visible in the cart.

To fill YOUR account's cart (not an anonymous guest cart) set the FIRSTCRY_STATE_B64 secret
(see login_helper.py). The bot never checks out or pays.
"""
import base64
import gzip
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

import scraper
from scraper import DESKTOP_CTX, debug_dump

CART_ATTEMPTS = int(os.getenv("CART_ATTEMPTS") or 10)
CART_URL = "https://checkout.firstcry.com/pay"
SHORTLIST_URL = "https://www.firstcry.com/myshortlist"
LISTING_URLS = scraper.PLATFORMS[0]["urls"]

ADD_BTN = re.compile(r"^\s*(add to cart)\s*$", re.I)
GO_CART = re.compile(r"^\s*(go to cart|view cart)\s*$", re.I)
OOS_RE = re.compile(r"^\s*(notify me|out of stock|sold out|currently unavailable)\s*$", re.I)
UNDELIVERABLE_RE = re.compile(r"not deliverable|undeliverable|not serviceable|cannot be delivered|can.?t be delivered", re.I)
SHOW_MORE = re.compile(r"^\s*show more products\s*$", re.I)


def _state_path():
    raw = (os.getenv("FIRSTCRY_STATE_B64") or "").strip()
    if not raw:
        return None
    try:
        data = base64.b64decode(raw)
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        json.loads(data)
        path = Path(tempfile.gettempdir()) / "fc_state.json"
        path.write_bytes(data)
        return str(path)
    except Exception as e:
        logging.error(f"FIRSTCRY_STATE_B64 is invalid ({type(e).__name__}); using a guest session")
        return None


async def _first_visible(loc, limit: int = 10):
    try:
        n = min(await loc.count(), limit)
    except Exception:
        return None
    for i in range(n):
        el = loc.nth(i)
        try:
            if await el.is_visible():
                return el
        except Exception:
            pass
    return None


async def _settle(page, ms: int = 800):
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)   # FirstCry never fully idles; don't wait long
    except Exception:
        pass
    await page.wait_for_timeout(ms)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _title_key(item: dict) -> str:
    return _norm(item["title"])[:45]


# =========================================================================== JS used on the pages
# Finds the Shortlist control of product `pid` on the LISTING (text, onclick, label, class, CSS text).
_CARD_SHORTLIST_JS = r"""
(pid) => {
  document.querySelectorAll("[data-hw-sl]").forEach(x => x.removeAttribute("data-hw-sl"));
  const PID = /\/(\d+)\/product-detail/;
  const links = [...document.querySelectorAll("a[href*='/product-detail']")].filter(a => a.querySelector("img"));
  const pids = links.map(a => (PID.exec(a.getAttribute("href") || "") || [])[1]);
  const FOLLOW = Node.DOCUMENT_POSITION_FOLLOWING, PREC = Node.DOCUMENT_POSITION_PRECEDING;
  const SL = /short\s*-?_?list|wish\s*-?_?list/i;
  const STATE = /shortlisted|my\s*shortlist/i;
  const own = el => [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.nodeValue.trim()).filter(Boolean).join(" ");
  const shown = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none"; };
  let anyCard = false, anyOos = false, anyAdd = false;
  for (let i = 0; i < links.length; i++) {
    if (pids[i] !== pid) continue;
    anyCard = true;
    const mine = links[i];
    let next = null;
    for (let j = i + 1; j < links.length; j++) { if (pids[j] !== pid) { next = links[j]; break; } }
    const els = [...document.querySelectorAll("body *")].filter(el =>
      !/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(el.tagName) &&
      (mine.compareDocumentPosition(el) & FOLLOW) &&
      (!next || ((next.compareDocumentPosition(el) & PREC) && !el.contains(next))));
    for (const el of els) {
      const t = own(el);
      if (/^add to cart$/i.test(t)) anyAdd = true;
      if (/notify me|out of stock|sold out/i.test(t)) anyOos = true;
    }
    const tiers = [
      ["text",     els.filter(el => /^(add to )?shortlist$/i.test(own(el)))],
      ["textlike", els.filter(el => SL.test(own(el)) && !STATE.test(own(el)))],
      ["onclick",  els.filter(el => SL.test(el.getAttribute("onclick") || "") && !STATE.test(own(el)))],
      ["label",    els.filter(el => SL.test((el.getAttribute("title") || "") + " " + (el.getAttribute("aria-label") || "") + " " + (el.getAttribute("alt") || "")) && !STATE.test(own(el)))],
      ["class",    els.filter(el => SL.test(String((el.className && el.className.baseVal !== undefined) ? el.className.baseVal : el.className) + " " + (el.id || "")) && !STATE.test(String(el.className) + own(el)))],
      ["pseudo",   els.filter(el => ["::before", "::after"].some(p => SL.test(getComputedStyle(el, p).content || "")))],
    ];
    for (const [how, list] of tiers) {
      if (!list.length) continue;
      const pick = list.find(shown) || list[0];
      pick.scrollIntoView({block: "center"});
      pick.setAttribute("data-hw-sl", "1");
      return "ok:" + how;
    }
  }
  if (!anyCard) return "no-card";
  if (anyOos && !anyAdd) return "oos";
  return "no-button";
}
"""

# Same control on a product page (used when a car has no card of its own on the listing).
_PAGE_SHORTLIST_JS = r"""
() => {
  document.querySelectorAll("[data-hw-sl]").forEach(x => x.removeAttribute("data-hw-sl"));
  const SL = /short\s*-?_?list|wish\s*-?_?list/i, STATE = /shortlisted|my\s*shortlist/i;
  const own = el => [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.nodeValue.trim()).filter(Boolean).join(" ");
  const shown = el => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none"; };
  // never click the header's Shortlist link or the bottom bar's Shortlist TAB (that only opens the side panel)
  const excluded = el => { for (let e = el; e; e = e.parentElement) {
      if (getComputedStyle(e).position === "fixed") return true;
      if (e.tagName === "HEADER" || e.tagName === "NAV") return true;
      if (e.matches && e.matches("a[href*='myshortlist']")) return true; } return false; };
  const all = [...document.querySelectorAll("body *")].filter(el =>
      !/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(el.tagName) && !excluded(el));
  const cls = el => String((el.className && el.className.baseVal !== undefined) ? el.className.baseVal : el.className);
  const icon = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.width < 90 && r.height > 0 && r.height < 90; };
  const tiers = [
    ["text",    all.filter(el => /^(add to )?(shortlist|wishlist)$/i.test(own(el)))],
    ["onclick", all.filter(el => SL.test(el.getAttribute("onclick") || "") && !STATE.test(own(el)))],
    ["label",   all.filter(el => SL.test((el.getAttribute("title") || "") + " " + (el.getAttribute("aria-label") || "") + " " + (el.getAttribute("alt") || "")) && !STATE.test(own(el)))],
    ["class",   all.filter(el => SL.test(cls(el) + " " + (el.id || "")) && !STATE.test(cls(el) + own(el)))],
    ["pseudo",  all.filter(el => ["::before", "::after"].some(p => SL.test(getComputedStyle(el, p).content || "")))],
    ["heart",   all.filter(el => /heart|favou?rite|\bfav\b/i.test(cls(el) + " " + (el.id || "")) && icon(el) && !STATE.test(cls(el)))],
  ];
  for (const [how, list] of tiers) {
    const vis = list.filter(el => shown(el) && el.getBoundingClientRect().top < 900);
    const pick = vis[0] || list.find(shown);
    if (!pick) continue;
    pick.scrollIntoView({block: "center"});
    pick.setAttribute("data-hw-sl", "1");
    return "ok:" + how;
  }
  return "no-button";
}
"""

# What is inside this car's card (for debugging): texts, controls, HTML.
_SEGMENT_PROBE_JS = r"""
(pid) => {
  const PID = /\/(\d+)\/product-detail/;
  const links = [...document.querySelectorAll("a[href*='/product-detail']")].filter(a => a.querySelector("img"));
  const pids = links.map(a => (PID.exec(a.getAttribute("href") || "") || [])[1]);
  const i = pids.indexOf(pid);
  if (i < 0) return {error: "no card for pid " + pid};
  const mine = links[i];
  let next = null;
  for (let j = i + 1; j < links.length; j++) { if (pids[j] !== pid) { next = links[j]; break; } }
  const FOLLOW = Node.DOCUMENT_POSITION_FOLLOWING, PREC = Node.DOCUMENT_POSITION_PRECEDING;
  const els = [...document.querySelectorAll("body *")].filter(el =>
    !/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(el.tagName) &&
    (mine.compareDocumentPosition(el) & FOLLOW) && (!next || ((next.compareDocumentPosition(el) & PREC) && !el.contains(next))));
  const own = el => [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.nodeValue.trim()).filter(Boolean).join(" ");
  const texts = [...new Set(els.map(own).filter(Boolean))].slice(0, 80);
  const hint = /short|wish|heart|fav|save|like/i;
  const controls = els.filter(el => hint.test(String(el.className) + " " + (el.id || "") + " " + (el.getAttribute("onclick") || "")
                    + " " + (el.getAttribute("title") || "") + " " + (el.getAttribute("aria-label") || "")))
    .slice(0, 30).map(el => ({tag: el.tagName, cls: String(el.className).slice(0, 120), id: el.id || "",
       text: own(el).slice(0, 60), onclick: (el.getAttribute("onclick") || "").slice(0, 160),
       title: el.getAttribute("title") || "", aria: el.getAttribute("aria-label") || ""}));
  let wrap = mine, out = "";
  for (let k = 0; k < 8 && wrap.parentElement; k++, wrap = wrap.parentElement) {
    if (next && wrap.contains(next)) break;
    out = wrap.outerHTML;
  }
  return {texts, controls, html: out.slice(0, 14000)};
}
"""

# After a Shortlist click: did a login popup appear? does the card now show "Shortlisted"?
_CARD_STATE_JS = r"""
(pid) => {
  let popup = "";
  for (const e of document.querySelectorAll("body *")) {
    const cs = getComputedStyle(e);
    if (cs.position !== "fixed" && cs.position !== "absolute") continue;
    if ((parseInt(cs.zIndex || "0", 10) || 0) < 50) continue;
    const r = e.getBoundingClientRect();
    if (r.width < 150 || r.height < 80 || cs.display === "none" || cs.visibility === "hidden") continue;
    const t = (e.innerText || "").trim();
    if (/log\s*in|sign\s*in|register|otp|mobile\s*(no|number)/i.test(t)) { popup = t.slice(0, 220); break; }
  }
  const PID = /\/(\d+)\/product-detail/;
  const links = [...document.querySelectorAll("a[href*='/product-detail']")].filter(a => a.querySelector("img"));
  const pids = links.map(a => (PID.exec(a.getAttribute("href") || "") || [])[1]);
  const i = pids.indexOf(pid);
  let badge = null;
  if (i >= 0) {
    const mine = links[i];
    let next = null;
    for (let j = i + 1; j < links.length; j++) { if (pids[j] !== pid) { next = links[j]; break; } }
    const FOLLOW = Node.DOCUMENT_POSITION_FOLLOWING, PREC = Node.DOCUMENT_POSITION_PRECEDING;
    badge = false;
    for (const el of document.querySelectorAll("body *")) {
      if (/^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(el.tagName)) continue;
      if (!(mine.compareDocumentPosition(el) & FOLLOW)) continue;
      if (next && (!(next.compareDocumentPosition(el) & PREC) || el.contains(next))) continue;
      const own = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.nodeValue.trim()).join(" ");
      if (/^shortlisted$/i.test(own)) {
        const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
        if (r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none") { badge = true; break; }
      }
    }
  }
  return {popup, badge};
}
"""

_SHORTLIST_PROBE_JS = r"""
() => ({
  url: location.href,
  title: document.title,
  text: (document.body.innerText || "").slice(0, 2500),
  product_links: [...document.querySelectorAll("a[href]")].map(a => a.getAttribute("href"))
                  .filter(h => /product-detail|pdp|product/.test(h || "")).slice(0, 40),
  images: [...document.images].map(i => i.currentSrc || i.src).slice(0, 30),
})
"""

_PAGE_PROBE_JS = r"""
() => {
  const own = el => [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.nodeValue.trim()).filter(Boolean).join(" ");
  const hint = /short|wish|heart|fav|save|like/i;
  const controls = [...document.querySelectorAll("body *")].filter(el => !/^(SCRIPT|STYLE|NOSCRIPT)$/.test(el.tagName) &&
      hint.test(String(el.className) + " " + (el.id || "") + " " + (el.getAttribute("onclick") || "") + " " + (el.getAttribute("title") || "") + " " + (el.getAttribute("aria-label") || "")))
    .slice(0, 40).map(el => { const r = el.getBoundingClientRect();
      return {tag: el.tagName, cls: String(el.className).slice(0, 120), id: el.id || "", text: own(el).slice(0, 50),
              onclick: (el.getAttribute("onclick") || "").slice(0, 140), title: el.getAttribute("title") || "",
              rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]}; });
  return {url: location.href, controls};
}
"""

_WHOAMI_JS = r"""
() => {
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n, txt = "";
  while ((n = w.nextNode())) {
    const p = n.parentElement;
    if (!p || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(p.tagName)) continue;
    txt += " " + n.nodeValue;
    if (txt.length > 400000) break;
  }
  const emails = (txt.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) || []).filter(e => !/firstcry|brainbees|example\.com|sentry/i.test(e));
  const phones = txt.match(/(?<!\d)[6-9]\d{9}(?!\d)/g) || [];
  return {emails: [...new Set(emails)].slice(0, 2), phones: [...new Set(phones)].slice(0, 2)};
}
"""

_HAS_CARD_JS = r"""
(pid) => [...document.querySelectorAll("a[href*='/" + pid + "/product-detail']")].some(a => a.querySelector("img"))
"""

# Is the car on the My Shortlist page? (FirstCry's My Shortlist has NO product links, so also match the name.)
_PAGE_HAS_JS = r"""
({pid, key}) => {
  if (document.querySelector("a[href*='/" + pid + "/']")) return "href";
  if (document.querySelector("img[src*='" + pid + "']")) return "img";
  if (document.querySelector("[href*='" + pid + "'], [data-pid='" + pid + "'], [data-productid='" + pid + "']")) return "attr";
  if (key) {
    const txt = (document.body.innerText || "").toLowerCase().replace(/[^a-z0-9]+/g, " ");
    if (txt.includes(key)) return "title";
  }
  return "";
}
"""

# The car's ROW on My Shortlist. Reads the text that follows the car's name, up to the next car:
#   "ADD TO CART"                     -> buyable here  (tags that button)
#   "Undeliverable / not deliverable" -> cannot be delivered to the session's pincode
#   "NOTIFY ME / OUT OF STOCK"        -> out of stock
_SHORTLIST_ROW_JS = r"""
({key}) => {
  document.querySelectorAll("[data-hw-target]").forEach(x => x.removeAttribute("data-hw-target"));
  const norm = s => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const shown = x => { const r = x.getBoundingClientRect(), cs = getComputedStyle(x);
                       return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && cs.display !== "none"; };
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n, titleEl = null, add = null, flags = "";
  while ((n = w.nextNode())) {
    const p = n.parentElement;
    if (!p || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(p.tagName)) continue;
    const t = (n.nodeValue || "").trim();
    if (!t) continue;
    if (!titleEl) { if (key && norm(t).includes(key) && shown(p)) titleEl = p; continue; }   // the VISIBLE title only
    // next car, or the end of the list: stop (page footer / hidden shortlist panel must never be read)
    if (/^hot wheels/i.test(t) || /^(international|remove all|categories)$/i.test(t) || /^\d+ left$/i.test(t) ||
        /^your shortlist is full/i.test(t) || /^products successfully added/i.test(t)) break;
    if (!add && /^add to cart$/i.test(t) && shown(p)) {
      const a = p.getBoundingClientRect(), b = titleEl.getBoundingClientRect();
      const sameColumn = Math.abs((a.left + a.width / 2) - (b.left + b.width / 2)) < 110;
      if (sameColumn && a.top >= b.top - 5 && a.top - b.top < 450) add = p;
    }
    flags += " " + t.toLowerCase();
  }
  if (!titleEl) return "not-listed";
  if (add) { add.setAttribute("data-hw-target", "1"); return "ok"; }
  if (/undeliverable|not deliverable/.test(flags)) return "undeliverable";
  if (/out of stock|notify me/.test(flags)) return "oos";
  return "no-add-button";
}
"""

# Fallback for a My Shortlist layout where cars DO have links: nearest Add-to-cart to the car's link/image.
_SHORTLIST_ADD_ANCHOR_JS = r"""
({pid, key}) => {
  document.querySelectorAll("[data-hw-target]").forEach(x => x.removeAttribute("data-hw-target"));
  let anchor = document.querySelector("a[href*='/" + pid + "/']") || document.querySelector("img[src*='" + pid + "']")
            || document.querySelector("[href*='" + pid + "'], [data-pid='" + pid + "'], [data-productid='" + pid + "']");
  if (!anchor) return "not-listed";
  const isAdd = x => /^\s*(add to cart|move to cart|add to bag)\s*$/i.test((x.innerText || x.value || "").trim());
  const visible = x => x.offsetParent !== null || getComputedStyle(x).position === "fixed";
  const centre = x => { const r = x.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
  const dist = (a, b) => { const p = centre(a), q = centre(b); return Math.hypot(p[0] - q[0], p[1] - q[1]); };
  const nearest = list => list.slice().sort((a, b) => dist(a, anchor) - dist(b, anchor))[0];
  const ADD_SEL = "button, a, div, span, input";
  let box = anchor;
  for (let depth = 0; depth < 10 && box; depth++, box = box.parentElement) {
    const adds = [...box.querySelectorAll(ADD_SEL)].filter(x => isAdd(x) && visible(x));
    if (!adds.length) continue;
    const pick = nearest(adds);
    const cbs = [...box.querySelectorAll("input[type=checkbox]")];
    if (cbs.length) { const cb = nearest(cbs); if (!cb.checked) cb.click(); }
    pick.setAttribute("data-hw-target", "1");
    return "ok";
  }
  return "no-add-button";
}
"""


# =========================================================================== small page helpers
async def _click_tagged(page, selector: str) -> bool:
    loc = page.locator(selector).first
    try:
        await loc.click(timeout=3000)
        return True
    except Exception:
        try:
            await loc.dispatch_event("click")      # hidden-until-hover buttons
            return True
        except Exception:
            return False


async def _is_logged_in(page):
    """True/False from the header; None if it could not be determined."""
    try:
        if await _first_visible(page.get_by_text(re.compile(r"^\s*login\s*/\s*register\s*$", re.I))):
            return False
        if await _first_visible(page.get_by_text(re.compile(r"my account", re.I))):
            return True
    except Exception:
        pass
    return None


def _mask(s: str) -> str:
    if "@" in s:
        user, dom = s.split("@", 1)
        return user[:2] + "•••@" + dom
    return s[:2] + "••••••" + s[-3:]


async def _whoami(page):
    """Masked email / phone found in the logged-in page, to compare with the account you use in your browser."""
    try:
        r = await page.evaluate(_WHOAMI_JS)
        found = [_mask(x) for x in (r.get("emails", []) + r.get("phones", []))]
        return ", ".join(found) if found else None
    except Exception:
        return None


async def _header_text(page) -> str:
    try:
        return await page.evaluate("() => (document.body.innerText || '').slice(0, 900)")
    except Exception:
        return ""


async def _read_pincode(page):
    m = re.search(r"select location\s*(\d{6})", await _header_text(page), re.I)
    return m.group(1) if m else None


async def _cart_count(page):
    """The number next to the Cart icon in the header (None if not found)."""
    m = re.search(r"(\d+)\s*Cart\b", await _header_text(page), re.I)
    return int(m.group(1)) if m else None


async def _cart_page_has(page, item: dict) -> str:
    """
    Open the cart page and say what it shows: 'yes' (this car is listed), 'empty' ("No items in your cart"),
    or 'other' (items shown but not this car / could not tell). Polls a few seconds because the cart loads late.
    """
    try:
        await page.goto(CART_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        logging.warning(f"[cart] cart page failed to load: {e}")
        return "other"
    await _settle(page, 800)
    key = _norm(item["title"])[:40]
    end = time.monotonic() + 5
    state = "other"
    while True:
        try:
            body = _norm(await page.inner_text("body"))
            if await page.locator(f"a[href*='/{item['id']}/'], img[src*='{item['id']}']").count() or (key and key in body):
                return "yes"
            state = "empty" if ("no items in your cart" in body or "your cart is empty" in body) else "other"
        except Exception:
            state = "other"
        if time.monotonic() > end:
            return state
        await page.wait_for_timeout(900)


async def _shortlist_has(page, item: dict, wait_s: float = 5.0) -> bool:
    """Is the car on My Shortlist? Checks link, image, attribute and car name; waits for the list to fill in."""
    await page.goto(SHORTLIST_URL, wait_until="domcontentloaded", timeout=30000)
    await _settle(page, 600)
    args = {"pid": item["id"], "key": _title_key(item)}
    end = time.monotonic() + wait_s
    while True:
        try:
            if await page.evaluate(_PAGE_HAS_JS, args):
                return True
        except Exception:
            pass
        if time.monotonic() > end:
            return False
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        await page.wait_for_timeout(900)


async def _check_delivery(page):
    """On a product page: enter the pincode in the delivery box (if there is one) so availability is real."""
    try:
        box = page.locator("input[placeholder*='incode' i]:visible").first
        if not await box.count():
            return
        await box.fill(scraper.PINCODE, timeout=2500)
        btn = page.locator("button:visible, a:visible, div[role=button]:visible, span:visible").filter(
            has_text=re.compile(r"^\s*(check|apply)\s*$", re.I)).first
        try:
            await btn.click(timeout=2000)
        except Exception:
            await box.press("Enter")
        await page.wait_for_timeout(1500)
    except Exception:
        pass


# =========================================================================== stock verification
class Verifier:
    """Guest browser page used to confirm that a listing's 'in stock' is real."""

    def __init__(self, ctx, page):
        self.ctx, self.page = ctx, page

    @classmethod
    async def create(cls, browser):
        ctx = await browser.new_context(**DESKTOP_CTX)
        return cls(ctx, await ctx.new_page())

    async def status(self, item: dict) -> str:
        """'in' (ADD TO CART visible), 'undeliverable', 'out' (notify / out of stock visible) or 'unknown'."""
        page = self.page
        try:
            await page.goto(item["link"], wait_until="domcontentloaded", timeout=30000)
            await _settle(page, 600)
            await _check_delivery(page)
            if await _first_visible(page.get_by_text(ADD_BTN)) or await _first_visible(page.get_by_text(GO_CART)):
                return "in"
            und = await _first_visible(page.get_by_text(UNDELIVERABLE_RE))
            if und is not None:
                try:
                    if len((await und.inner_text()).strip()) <= 80:      # a status line, not FAQ text
                        return "undeliverable"
                except Exception:
                    pass
            if await _first_visible(page.get_by_text(OOS_RE)):
                return "out"
            return "unknown"
        except Exception as e:
            logging.warning(f"[verify] {item['id']}: {e}")
            return "unknown"

    async def close(self):
        await self.ctx.close()


# =========================================================================== the cart session
class CartSession:
    """One browser context (logged in if FIRSTCRY_STATE_B64 is set) shared by all cars in a run."""

    def __init__(self, ctx, listing, shortlist, cart, logged_in_state, browser=None, state_path=None):
        self.ctx, self.listing, self.shortlist, self.cart = ctx, listing, shortlist, cart
        self.logged_in = logged_in_state          # False = guest (no state), None = unknown yet
        self.pincode = None                       # delivery pincode shown in FirstCry's header
        self.account = None                       # masked account hint, e.g. "sa•••@gmail.com"
        self.browser, self.state_path = browser, state_path
        self._persist = None                      # cached result of the "does YOUR cart see it?" test
        self._listing_url = None
        self._pin_done = False

    @classmethod
    async def create(cls, browser):
        state = _state_path()
        args = dict(DESKTOP_CTX)
        if state:
            args["storage_state"] = state
        ctx = await browser.new_context(**args)
        return cls(ctx, await ctx.new_page(), await ctx.new_page(), await ctx.new_page(),
                   None if state else False, browser, state)

    async def close(self):
        await self.ctx.close()

    # ---- finding the car on the listing
    async def _find_card(self, item: dict, deadline: float) -> str:
        pid = item["id"]
        for url in ([self._listing_url] if self._listing_url else []) + LISTING_URLS:
            if url != self._listing_url:
                await self.listing.goto(url, wait_until="domcontentloaded", timeout=30000)
                await _settle(self.listing, 800)
                self._listing_url = url
                if not self._pin_done:
                    self._pin_done = True
                    if await scraper._set_pincode(self.listing, "cart"):
                        await self.listing.reload(wait_until="domcontentloaded")
                        await _settle(self.listing, 800)
                if self.logged_in is None:
                    self.logged_in = await _is_logged_in(self.listing)
                if self.state_path and not self.account:
                    self.account = await _whoami(self.listing)
                self.pincode = await _read_pincode(self.listing) or self.pincode
            stable, last = 0, -1
            for _ in range(90):
                if time.monotonic() > deadline:
                    return "timeout"
                if await self.listing.evaluate(_HAS_CARD_JS, pid):
                    return "found"
                cnt = await self.listing.evaluate(scraper.FC_COUNT_JS)
                clicked = False
                try:
                    more = self.listing.get_by_text(SHOW_MORE).first
                    if await more.count() and await more.is_visible():
                        await more.scroll_into_view_if_needed(timeout=2000)
                        await more.click(timeout=3000)
                        clicked = True
                    else:
                        await self.listing.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass
                # only give up when the list has stopped growing AND there is no 'Show more' button left to press
                stable = stable + 1 if (cnt == last and not clicked) else 0
                last = cnt
                if stable >= 4:
                    break
                await self.listing.wait_for_timeout(1000)
            self._listing_url = None            # not on this page; try the next listing URL
        return "not-found"

    async def _locate(self, item: dict, deadline: float, res: dict):
        """Returns 'card' or 'product' (where the Shortlist button will be clicked), or None on failure."""
        where = await self._find_card(item, deadline)
        res["logged_in"] = self.logged_in
        if where == "found":
            return "card"
        if where == "not-found":
            self._listing_url = None
            await self.listing.goto(item["link"], wait_until="domcontentloaded", timeout=30000)
            await _settle(self.listing, 600)
            logging.info(f"[cart] {item['id']}: no listing card, using the product page's Shortlist button")
            return "product"
        res["detail"] = f"could not load the listing ({where})"
        return None

    # ---- evidence for debugging
    async def _dump_card(self, item: dict, mode: str):
        evidence = {}
        try:
            scraper.DEBUG_DIR.mkdir(exist_ok=True)
            if mode == "card":
                probe = await self.listing.evaluate(_SEGMENT_PROBE_JS, item["id"])
                evidence["card_html"] = (probe.get("html") or "(none)") if isinstance(probe, dict) else str(probe)
                probe_txt = json.dumps({k: v for k, v in probe.items() if k != "html"}, indent=1, ensure_ascii=False) \
                    if isinstance(probe, dict) else str(probe)
                evidence["card_html_name"] = f"card_{item['id']}.html"
                (scraper.DEBUG_DIR / f"card_{item['id']}.html").write_text(evidence["card_html"], encoding="utf-8")
            else:
                probe_txt = json.dumps(await self.listing.evaluate(_PAGE_PROBE_JS), indent=1, ensure_ascii=False)
            evidence["probe"] = probe_txt
            (scraper.DEBUG_DIR / f"probe_{item['id']}.json").write_text(probe_txt, encoding="utf-8")
            await debug_dump(self.listing, f"listing_{item['id']}", force=True)
            evidence["listing_png"] = await self.listing.screenshot()
        except Exception as e:
            logging.warning(f"[cart] card dump failed: {e}")
        return evidence

    async def _shortlist_evidence(self, item: dict, state: dict) -> dict:
        ev = {}
        try:
            probe = await self.shortlist.evaluate(_SHORTLIST_PROBE_JS)
            ev["shortlist_probe"] = json.dumps({"card_state_after_click": state, "shortlist_page": probe},
                                               indent=1, ensure_ascii=False)
            ev["listing_png"] = await self.listing.screenshot()
            ev["shortlist_png"] = await self.shortlist.screenshot()
        except Exception as e:
            logging.warning(f"[cart] shortlist evidence failed: {e}")
        return ev

    # ---- step 1: make sure the car is on My Shortlist
    async def _shortlist_step(self, item: dict, mode: str, attempt: int, res: dict) -> str:
        """Returns 'ready' (car is on My Shortlist, page loaded), 'retry' (res['detail'] set) or 'final' (res final)."""
        pid = item["id"]
        if await _shortlist_has(self.shortlist, item):
            return "ready"
        # Clicking Shortlist on a car that is already shortlisted would REMOVE it: check the card's state first.
        state = await self.listing.evaluate(_CARD_STATE_JS, pid) if mode == "card" else {"popup": "", "badge": None}
        if state.get("popup"):                       # leftover popup from an earlier click
            await self.listing.keyboard.press("Escape")
            await self.listing.wait_for_timeout(300)
            if mode == "card":
                state = await self.listing.evaluate(_CARD_STATE_JS, pid)
        before_popup = state.get("popup", "")

        if not state.get("badge"):
            if mode == "card":
                r = await self.listing.evaluate(_CARD_SHORTLIST_JS, pid)
                if r == "no-button":
                    try:                             # some sites only inject the control on hover
                        await self.listing.locator(f"a[href*='/{pid}/product-detail']:has(img)").first.hover(timeout=3000)
                        await self.listing.wait_for_timeout(500)
                        r = await self.listing.evaluate(_CARD_SHORTLIST_JS, pid)
                    except Exception:
                        pass
            else:
                r = await self.listing.evaluate(_PAGE_SHORTLIST_JS)
            if r == "oos":
                res["status"], res["detail"] = "sold_out", "listing card shows out of stock"
                return "final"
            if not str(r).startswith("ok"):
                res["detail"] = f"shortlist control: {r} ({mode})"
                if attempt == 1:
                    res["evidence"] = await self._dump_card(item, mode)
                return "retry"
            logging.info(f"[cart] {pid}: Shortlist control found via {r}")
            if not await _click_tagged(self.listing, "[data-hw-sl='1']"):
                res["detail"] = "could not click Shortlist"
                return "retry"
            await self.listing.wait_for_timeout(1500)
        else:
            logging.info(f"[cart] {pid}: card already shows Shortlisted; re-checking My Shortlist")
            await self.listing.wait_for_timeout(1500)

        state = await self.listing.evaluate(_CARD_STATE_JS, pid) if mode == "card" else {"popup": "", "badge": None}
        if state.get("popup") and not before_popup:      # a NEW login prompt appeared after the click
            res["logged_in"] = False
            res["detail"] = ("FirstCry asked to log in after clicking Shortlist: the saved session was rejected "
                             "or expired (logged-in session needed). Re-run login_helper.py and update FIRSTCRY_STATE_B64.")
            res["evidence"] = await self._shortlist_evidence(item, state)
            return "final"
        if await _shortlist_has(self.shortlist, item):
            return "ready"
        await self.shortlist.wait_for_timeout(2500)
        if await _shortlist_has(self.shortlist, item):
            return "ready"
        res["detail"] = (f"clicked Shortlist (card shows Shortlisted: {state.get('badge')}) but the car "
                         f"is not on My Shortlist")
        if attempt == 1:
            res["evidence"] = {**res.get("evidence", {}), **(await self._shortlist_evidence(item, state))}
        return "retry"

    async def _row_state(self, item: dict) -> str:
        """On the loaded My Shortlist page: 'ok' (Add to cart button tagged), 'undeliverable', 'oos', ..."""
        r = await self.shortlist.evaluate(_SHORTLIST_ROW_JS, {"key": _title_key(item)})
        if r == "not-listed":
            r = await self.shortlist.evaluate(_SHORTLIST_ADD_ANCHOR_JS, {"pid": item["id"], "key": _title_key(item)})
        return r

    # ---- gate: is the car really buyable at this delivery pincode?
    async def gate(self, item: dict, deadline: float) -> dict:
        """Shortlists the car and reads its row. state: 'ok' | 'undeliverable' | 'oos' | 'unknown'."""
        res = {"status": "failed", "tries": 0, "logged_in": self.logged_in, "detail": ""}
        try:
            mode = await self._locate(item, deadline, res)
            if mode is None:
                return {"state": "unknown", "pincode": self.pincode, "detail": res["detail"]}
            for attempt in (1, 2):
                if time.monotonic() > deadline:
                    break
                st = await self._shortlist_step(item, mode, attempt, res)
                if st == "final":
                    return {"state": "oos" if res["status"] == "sold_out" else "unknown",
                            "pincode": self.pincode, "detail": res["detail"], "evidence": res.get("evidence")}
                if st == "retry":
                    continue
                row = await self._row_state(item)
                logging.info(f"[gate] {item['id']}: My Shortlist row -> {row}")
                state = {"ok": "ok", "undeliverable": "undeliverable", "oos": "oos"}.get(row, "unknown")
                return {"state": state, "pincode": self.pincode, "detail": row}
            return {"state": "unknown", "pincode": self.pincode, "detail": res["detail"]}
        except Exception as e:
            logging.warning(f"[gate] {item['id']}: {e}")
            return {"state": "unknown", "pincode": self.pincode, "detail": str(e)}

    # ---- strict check that the car really went into the cart
    async def _verify_added(self, item: dict) -> bool:
        """The cart page itself must list the car (a counter, or a name somewhere else on a page, is not proof)."""
        for _ in range(3):
            if await _cart_page_has(self.cart, item) == "yes":
                return True
            await self.cart.wait_for_timeout(1200)
        return False

    async def _persist_check(self, item: dict):
        """
        Open the cart in a BRAND-NEW browser session that only has the saved login (what your own browser has).
        True = it shows the car, False = that session's cart is empty, None = could not tell / guest.
        """
        if not self.state_path or not self.browser:
            return None
        if self._persist is not None:
            return self._persist
        ctx = None
        try:
            args = dict(DESKTOP_CTX)
            args["storage_state"] = self.state_path
            ctx = await self.browser.new_context(**args)
            page = await ctx.new_page()
            seen = await _cart_page_has(page, item)
            result = True if seen == "yes" else (False if seen == "empty" else None)
            logging.info(f"[cart] fresh-session cart check -> {seen}")
            self._persist = result
            return result
        except Exception as e:
            logging.warning(f"[cart] persistence check failed: {e}")
            return None
        finally:
            if ctx:
                try:
                    await ctx.close()
                except Exception:
                    pass

    async def _restore_shortlist(self, item: dict, deadline: float) -> bool:
        """The bot's 'Add to cart' moved the car OUT of your Shortlist. Put it back so it is not lost."""
        tmp = {"detail": ""}
        try:
            self._listing_url = None                      # reload the listing so the card state is fresh
            mode = await self._locate(item, deadline, tmp)
            if mode is None:
                return False
            return await self._shortlist_step(item, mode, 2, tmp) == "ready"
        except Exception as e:
            logging.warning(f"[cart] restore shortlist failed: {e}")
            return False

    async def _finish_added(self, item: dict, res: dict, deadline: float) -> dict:
        """The bot's own session shows the car in its cart. Now check YOUR view of the cart."""
        res["account"] = self.account
        res["persisted"] = await self._persist_check(item)
        if res["persisted"] is False:
            res["status"] = "session_only"
            res["detail"] = "a fresh session using the same saved login sees an empty cart"
            res["restored"] = await self._restore_shortlist(item, max(deadline, time.monotonic() + 40))
        else:
            res["status"], res["detail"] = "added", ""
        try:
            await self.cart.goto(CART_URL, wait_until="domcontentloaded", timeout=30000)
            await _settle(self.cart, 1000)
            res["cart_png"] = await self.cart.screenshot()
        except Exception:
            pass
        return res

    # ---- the add-to-cart loop
    async def add(self, item: dict, deadline: float) -> dict:
        """
        Listing card -> Shortlist -> My Shortlist -> Add to cart -> verify (up to CART_ATTEMPTS times).
        Returns {status: added|sold_out|undeliverable|failed, tries, logged_in, pincode, detail, evidence?}
        """
        res = {"status": "failed", "tries": 0, "logged_in": self.logged_in, "pincode": self.pincode, "detail": ""}
        pid = item["id"]
        try:
            mode = await self._locate(item, deadline, res)
            if mode is None:
                return res
            if self._persist is False:
                # Known from an earlier car in this run: the bot's cart is NOT your cart. Moving cars into it would
                # only remove them from your Shortlist, so just make sure they are on your Shortlist.
                st = await self._shortlist_step(item, mode, 1, res)
                if st == "final":
                    return res
                res["account"] = self.account
                res["status"] = "shortlist_only" if st == "ready" else "failed"
                return res
            clicked_once, repeats, last_detail = False, 0, ""
            for attempt in range(1, CART_ATTEMPTS + 1):
                if time.monotonic() > deadline:
                    res["detail"] = "time budget reached"
                    break
                res["tries"] = attempt
                res["pincode"] = self.pincode

                if attempt > 1 and clicked_once:             # an earlier click may have worked late: never add twice
                    if await _cart_page_has(self.cart, item) == "yes":
                        return await self._finish_added(item, res, deadline)

                st = await self._shortlist_step(item, mode, attempt, res)
                if st == "final":
                    return res
                if st == "retry":
                    # a missing control is not going to appear by itself: stop after 4 identical failures
                    repeats = repeats + 1 if res["detail"] == last_detail else 1
                    last_detail = res["detail"]
                    if repeats >= 4 and res["detail"].startswith("shortlist control:"):
                        res["detail"] += " (stopped after 4 identical attempts)"
                        break
                    continue

                row = await self._row_state(item)
                if row == "undeliverable":
                    res["status"] = "undeliverable"
                    res["detail"] = "FirstCry lists this car as 'Undeliverable / Product not deliverable' for the delivery pincode"
                    return res
                if row == "oos":
                    res["status"], res["detail"] = "sold_out", "My Shortlist shows the car as out of stock"
                    return res
                if row != "ok":
                    res["detail"] = f"My Shortlist page: {row}"
                    if attempt == 1:
                        await debug_dump(self.shortlist, f"shortlist_{pid}", force=True)
                    continue

                if not await _click_tagged(self.shortlist, "[data-hw-target='1']"):
                    res["detail"] = "could not click Add to cart on My Shortlist"
                    continue
                clicked_once = True
                await self.shortlist.wait_for_timeout(1500)

                if await self._verify_added(item):
                    return await self._finish_added(item, res, deadline)
                res["detail"] = "clicked Add to cart on My Shortlist but the cart page does not list the car"

            await debug_dump(self.cart, f"cart_{pid}", force=True)
            return res
        except Exception as e:
            res["detail"] = f"error: {type(e).__name__}: {e}"
            logging.error(f"[cart] {pid}: {e}")
            return res
