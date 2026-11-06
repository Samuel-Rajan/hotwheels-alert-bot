"""
Stock verification + add-to-cart for FirstCry.

* Verifier     : opens the product page and confirms an ADD TO CART button really exists
                 (so we never alert for a car that is not actually buyable).
* CartSession  : puts a car in the cart WITHOUT opening the product page:
                   listing page card -> Shortlist (wishlist) -> My Shortlist -> Add to cart -> check cart
                 repeated up to CART_ATTEMPTS times (default 10) until the car is visible in the cart.

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
LOGIN_LINK = re.compile(r"^\s*login\s*/\s*register\s*$", re.I)
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
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await page.wait_for_timeout(ms)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# --------------------------------------------------------------------------- stock verification
class Verifier:
    """Guest browser page used to confirm that a listing's 'in stock' is real."""

    def __init__(self, ctx, page):
        self.ctx, self.page = ctx, page

    @classmethod
    async def create(cls, browser):
        ctx = await browser.new_context(**DESKTOP_CTX)
        return cls(ctx, await ctx.new_page())

    async def status(self, item: dict) -> str:
        """'in' (ADD TO CART visible), 'out' (notify/out-of-stock visible), or 'unknown'."""
        page = self.page
        try:
            await page.goto(item["link"], wait_until="domcontentloaded", timeout=30000)
            await _settle(page, 600)
            if await _first_visible(page.get_by_text(ADD_BTN)) or await _first_visible(page.get_by_text(GO_CART)):
                return "in"
            if await _first_visible(page.get_by_text(OOS_RE)):
                return "out"
            return "unknown"
        except Exception as e:
            logging.warning(f"[verify] {item['id']}: {e}")
            return "unknown"

    async def close(self):
        await self.ctx.close()


# --------------------------------------------------------------------------- page helpers (JS)
# Finds the Shortlist control inside the listing card of product `pid` and tags it data-hw-sl.
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

# Same control, but on a product page (used when a car has no card of its own on the listing).
_PAGE_SHORTLIST_JS = r"""
() => {
  document.querySelectorAll("[data-hw-sl]").forEach(x => x.removeAttribute("data-hw-sl"));
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n, found = null;
  while ((n = walker.nextNode())) {
    const t = (n.nodeValue || "").trim();
    const p = n.parentElement;
    if (!p || !/^(add to )?(shortlist|wishlist)$/i.test(t)) continue;
    if (p.closest("a[href*='myshortlist']")) continue;          // skip the header "Shortlist" link
    if (!found || (p.offsetParent !== null && found.offsetParent === null)) found = p;
  }
  if (!found) return "no-button";
  found.scrollIntoView({block: "center"});
  found.setAttribute("data-hw-sl", "1");
  return "ok";
}
"""

# Returns the card's HTML so a failed run leaves evidence of FirstCry's real markup.
_CARD_HTML_JS = r"""
(pid) => {
  const PID = /\/(\d+)\/product-detail/;
  const imgLinks = [...document.querySelectorAll("a[href*='/product-detail']")].filter(a => a.querySelector("img"));
  const mine = imgLinks.find(a => (PID.exec(a.getAttribute("href") || "") || [])[1] === pid);
  if (!mine) return "";
  let card = mine, depth = 0;
  while (card.parentElement && card.parentElement !== document.body && depth++ < 6) card = card.parentElement;
  const idx = imgLinks.indexOf(mine);
  let el = mine, out = "";
  for (let i = 0; i < 8 && el.parentElement; i++, el = el.parentElement) {
    const next = imgLinks[idx + 1];
    if (next && el.contains(next)) break;
    out = el.outerHTML;
  }
  return (out || card.outerHTML).slice(0, 14000);
}
"""

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
       title: el.getAttribute("title") || "", aria: el.getAttribute("aria-label") || "",
       shown: (() => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })()}));
  let wrap = mine, out = "";
  for (let k = 0; k < 8 && wrap.parentElement; k++, wrap = wrap.parentElement) {
    if (next && wrap.contains(next)) break;
    out = wrap.outerHTML;
  }
  return {texts, controls, html: out.slice(0, 14000)};
}
"""

_HAS_CARD_JS = r"""
(pid) => [...document.querySelectorAll("a[href*='/" + pid + "/product-detail']")].some(a => a.querySelector("img"))
"""

# On the My Shortlist page: finds the 'Add to cart' control belonging to product `pid` (ticks its checkbox).
_SHORTLIST_ADD_JS = r"""
(pid) => {
  document.querySelectorAll("[data-hw-target]").forEach(x => x.removeAttribute("data-hw-target"));
  const PID = /\/(\d+)\/product-detail/;
  const pidOf = a => { const m = PID.exec(a.getAttribute("href") || ""); return m ? m[1] : null; };
  const anchors = [...document.querySelectorAll("a[href]")].filter(a => pidOf(a) === pid);
  if (!anchors.length) return "not-listed";
  const isAdd = x => /^\s*(add to cart|move to cart|add to bag)\s*$/i.test((x.innerText || x.value || "").trim());
  const visible = x => x.offsetParent !== null || getComputedStyle(x).position === "fixed";
  const pidsIn = el => { const s = new Set(); el.querySelectorAll("a[href]").forEach(a => { const p = pidOf(a); if (p) s.add(p); }); return s; };
  for (const a of anchors) {
    let box = a, depth = 0;
    while (box.parentElement && box.parentElement !== document.body && depth++ < 12) {
      const s = pidsIn(box.parentElement);
      if (s.size > 1 || (s.size === 1 && !s.has(pid))) break;
      box = box.parentElement;
    }
    const cb = box.querySelector("input[type=checkbox]");
    const own = [...box.querySelectorAll("button, a, div, span, input")].find(x => isAdd(x) && visible(x));
    if (own) { if (cb && !cb.checked) cb.click(); own.setAttribute("data-hw-target", "1"); return "ok"; }
    if (cb) {
      if (!cb.checked) cb.click();
      const g = [...document.querySelectorAll("button, a, div, span, input")].find(x => isAdd(x) && visible(x));
      if (g) { g.setAttribute("data-hw-target", "1"); return "ok"; }
    }
  }
  return "no-add-button";
}
"""


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
        if await _first_visible(page.get_by_text(LOGIN_LINK)):
            return False
        if await _first_visible(page.get_by_text(re.compile(r"my account", re.I))):
            return True
    except Exception:
        pass
    return None


async def _cart_contains(cart_page, item: dict) -> bool:
    try:
        await cart_page.goto(CART_URL, wait_until="domcontentloaded", timeout=30000)
        await _settle(cart_page, 1000)
        if await cart_page.locator(f"a[href*='/{item['id']}/']").count():
            return True
        body = _norm(await cart_page.inner_text("body"))
        if "your cart is empty" in body:
            return False
        key = _norm(item["title"])[:28]
        return bool(key) and key in body
    except Exception as e:
        logging.warning(f"[cart] check failed: {e}")
        return False


async def _shortlist_has(page, item: dict) -> bool:
    await page.goto(SHORTLIST_URL, wait_until="domcontentloaded", timeout=30000)
    await _settle(page, 800)
    return await page.locator(f"a[href*='/{item['id']}/']").count() > 0


# --------------------------------------------------------------------------- the cart session
class CartSession:
    """One browser context (logged in if FIRSTCRY_STATE_B64 is set) shared by all cars in a run."""

    def __init__(self, ctx, listing, shortlist, cart, logged_in_state):
        self.ctx, self.listing, self.shortlist, self.cart = ctx, listing, shortlist, cart
        self.logged_in = logged_in_state          # False = guest (no state), None = unknown yet
        self._listing_url = None

    @classmethod
    async def create(cls, browser):
        state = _state_path()
        args = dict(DESKTOP_CTX)
        if state:
            args["storage_state"] = state
        ctx = await browser.new_context(**args)
        return cls(ctx, await ctx.new_page(), await ctx.new_page(), await ctx.new_page(),
                   None if state else False)

    async def close(self):
        await self.ctx.close()

    async def _find_card(self, item: dict, deadline: float) -> str:
        """Make sure the car's card is on the listing page (loading more products as needed)."""
        pid = item["id"]
        for url in ([self._listing_url] if self._listing_url else []) + LISTING_URLS:
            if url != self._listing_url:
                await self.listing.goto(url, wait_until="domcontentloaded", timeout=30000)
                await _settle(self.listing, 800)
                self._listing_url = url
                if self.logged_in is None:
                    self.logged_in = await _is_logged_in(self.listing)
            stable, last = 0, -1
            for _ in range(60):
                if time.monotonic() > deadline:
                    return "timeout"
                if await self.listing.evaluate(_HAS_CARD_JS, pid):
                    return "found"
                cnt = await self.listing.evaluate(scraper.FC_COUNT_JS)
                stable = stable + 1 if cnt == last else 0
                last = cnt
                if stable >= 3:
                    break
                try:
                    more = self.listing.get_by_text(SHOW_MORE).first
                    if await more.count() and await more.is_visible():
                        await more.scroll_into_view_if_needed(timeout=2000)
                        await more.click(timeout=3000)
                    else:
                        await self.listing.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                except Exception:
                    pass
                await self.listing.wait_for_timeout(1000)
            self._listing_url = None            # not on this page; try the next listing URL
        return "not-found"

    async def _dump_card(self, item: dict, mode: str):
        """Save what FirstCry's markup really looks like; returned so main.py can send it to Telegram."""
        evidence = {}
        try:
            scraper.DEBUG_DIR.mkdir(exist_ok=True)
            if mode == "card":
                probe = await self.listing.evaluate(_SEGMENT_PROBE_JS, item["id"])
                evidence["card_html"] = (probe.get("html") or "(none)") if isinstance(probe, dict) else str(probe)
                probe_txt = json.dumps({k: v for k, v in probe.items() if k != "html"}, indent=1, ensure_ascii=False) \
                    if isinstance(probe, dict) else str(probe)
                evidence["probe"] = probe_txt
                (scraper.DEBUG_DIR / f"card_{item['id']}.html").write_text(evidence["card_html"], encoding="utf-8")
                (scraper.DEBUG_DIR / f"probe_{item['id']}.json").write_text(probe_txt, encoding="utf-8")
            await debug_dump(self.listing, f"listing_{item['id']}", force=True)
        except Exception as e:
            logging.warning(f"[cart] card dump failed: {e}")
        return evidence

    async def add(self, item: dict, deadline: float) -> dict:
        """
        Listing card -> Shortlist -> My Shortlist -> Add to cart -> verify in cart.
        (Cars that have no card of their own on the listing use their product page's Shortlist button.)
        Returns {status: added|sold_out|failed, tries, logged_in, detail}
        """
        res = {"status": "failed", "tries": 0, "logged_in": self.logged_in, "detail": ""}
        pid = item["id"]
        try:
            where = await self._find_card(item, deadline)
            res["logged_in"] = self.logged_in
            mode = "card"
            if where == "not-found":
                mode = "product"
                self._listing_url = None
                await self.listing.goto(item["link"], wait_until="domcontentloaded", timeout=30000)
                await _settle(self.listing, 600)
                logging.info(f"[cart] {pid}: no listing card, using the product page's Shortlist button")
            elif where != "found":
                res["detail"] = f"could not load the listing ({where})"
                return res

            for attempt in range(1, CART_ATTEMPTS + 1):
                if time.monotonic() > deadline:
                    res["detail"] = "time budget reached"
                    break
                res["tries"] = attempt

                # 1) Shortlist it -- but only if it is not shortlisted yet
                #    (clicking Shortlist on an already-shortlisted car would remove it).
                already = await _shortlist_has(self.shortlist, item)
                if not already:
                    if mode == "card":
                        r = await self.listing.evaluate(_CARD_SHORTLIST_JS, pid)
                        if r == "no-button":
                            # some sites only inject the control on hover
                            try:
                                await self.listing.locator(f"a[href*='/{pid}/product-detail']:has(img)").first.hover(timeout=3000)
                                await self.listing.wait_for_timeout(500)
                                r = await self.listing.evaluate(_CARD_SHORTLIST_JS, pid)
                            except Exception:
                                pass
                    else:
                        r = await self.listing.evaluate(_PAGE_SHORTLIST_JS)
                    if r == "oos":
                        res["status"], res["detail"] = "sold_out", "listing card shows out of stock"
                        return res
                    if not str(r).startswith("ok"):
                        res["detail"] = f"shortlist control: {r} ({mode})"
                        if attempt == 1:
                            res["evidence"] = await self._dump_card(item, mode)
                        continue
                    logging.info(f"[cart] {pid}: Shortlist control found via {r}")
                    if not await _click_tagged(self.listing, "[data-hw-sl='1']"):
                        res["detail"] = "could not click Shortlist"
                        continue
                    await self.listing.wait_for_timeout(1200)
                    if not await _shortlist_has(self.shortlist, item):
                        res["detail"] = ("clicked Shortlist but the car is not on My Shortlist "
                                         "(shortlisting needs a logged-in session)")
                        continue

                # 2) Add it to the cart from the shortlist page
                r = await self.shortlist.evaluate(_SHORTLIST_ADD_JS, pid)
                if r != "ok":
                    res["detail"] = f"shortlist page: {r}"
                    if attempt == 1:
                        await debug_dump(self.shortlist, f"shortlist_{pid}", force=True)
                    continue
                if not await _click_tagged(self.shortlist, "[data-hw-target='1']"):
                    res["detail"] = "could not click Add to cart on the shortlist page"
                    continue
                await self.shortlist.wait_for_timeout(1500)

                # 3) Confirm it is really in the cart
                if await _cart_contains(self.cart, item):
                    res["status"], res["detail"] = "added", ""
                    return res
                res["detail"] = "added from shortlist but the car is not visible in the cart"

            await debug_dump(self.cart, f"cart_{pid}", force=True)
            return res
        except Exception as e:
            res["detail"] = f"error: {type(e).__name__}: {e}"
            logging.error(f"[cart] {pid}: {e}")
            return res
