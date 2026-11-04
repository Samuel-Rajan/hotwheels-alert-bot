"""
Stock verification + add-to-cart for FirstCry.

* Verifier   : opens the product page and confirms an ADD TO CART button really exists.
* add_to_cart: clicks ADD TO CART up to CART_ATTEMPTS times, confirming each time by opening the
               cart page. If the item never shows up, it falls back to Shortlist -> Add to cart,
               up to SHORTLIST_ATTEMPTS times.

To put items in YOUR account's cart (not an anonymous guest cart) set the FIRSTCRY_STATE_B64
secret (see login_helper.py). It never checks out or pays.
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

from scraper import DESKTOP_CTX, debug_dump

CART_ATTEMPTS = int(os.getenv("CART_ATTEMPTS") or 10)
SHORTLIST_ATTEMPTS = int(os.getenv("SHORTLIST_ATTEMPTS") or 10)
CART_URL = "https://checkout.firstcry.com/pay"
SHORTLIST_URL = "https://www.firstcry.com/myshortlist"

ADD_BTN = re.compile(r"^\s*(add to cart)\s*$", re.I)
GO_CART = re.compile(r"^\s*(go to cart|view cart)\s*$", re.I)
OOS_RE = re.compile(r"^\s*(notify me|out of stock|sold out|currently unavailable)\s*$", re.I)
SHORTLIST_BTN = re.compile(r"^\s*(add to )?(shortlist|wishlist)\s*$", re.I)
LOGIN_LINK = re.compile(r"^\s*login\s*/\s*register\s*$", re.I)


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


# --------------------------------------------------------------------------- cart helpers
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


_FIND_SHORTLIST_BTN_JS = r"""
(a) => {
  document.querySelectorAll("[data-hw-target]").forEach(x => x.removeAttribute("data-hw-target"));
  let el = a;
  for (let i = 0; i < 8 && el; i++, el = el.parentElement) {
    const cands = [...el.querySelectorAll("button, a, div, span, input")];
    const b = cands.find(x => /^\s*(add to cart|move to cart)\s*$/i.test((x.innerText || x.value || "").trim())
                              && x.offsetParent !== null);
    if (b) {
      const cb = el.querySelector("input[type=checkbox]");
      if (cb && !cb.checked) cb.click();
      b.setAttribute("data-hw-target", "1");
      return true;
    }
  }
  return false;
}
"""


async def add_to_cart(browser, item: dict, deadline: float) -> dict:
    """
    Returns {status: added|added_shortlist|sold_out|failed, cart_tries, shortlist_tries, logged_in, detail}.
    """
    state = _state_path()
    ctx_args = dict(DESKTOP_CTX)
    if state:
        ctx_args["storage_state"] = state
    ctx = await browser.new_context(**ctx_args)
    page, cart = await ctx.new_page(), await ctx.new_page()
    res = {"status": "failed", "cart_tries": 0, "shortlist_tries": 0, "logged_in": False if not state else None,
           "detail": ""}
    link = item["link"]
    try:
        # ---------------- Phase 1: ADD TO CART (up to CART_ATTEMPTS)
        for attempt in range(1, CART_ATTEMPTS + 1):
            if time.monotonic() > deadline:
                res["detail"] = "time budget reached"
                break
            res["cart_tries"] = attempt
            await page.goto(link, wait_until="domcontentloaded", timeout=30000)
            await _settle(page, 500)
            if state and res["logged_in"] is None:
                res["logged_in"] = await _is_logged_in(page)

            add = await _first_visible(page.get_by_text(ADD_BTN))
            if not add:
                if await _first_visible(page.get_by_text(OOS_RE)):
                    res["status"], res["detail"] = "sold_out", "product page shows out of stock"
                    return res
                res["detail"] = "ADD TO CART button not found"
                continue
            try:
                await add.click(timeout=4000)
            except Exception as e:
                res["detail"] = f"click failed: {type(e).__name__}"
                continue
            await page.wait_for_timeout(1500)
            if await _cart_contains(cart, item):
                res["status"] = "added"
                return res
            res["detail"] = "clicked ADD TO CART but item not visible in cart"

        # ---------------- Phase 2: Shortlist -> Add to cart (up to SHORTLIST_ATTEMPTS)
        for attempt in range(1, SHORTLIST_ATTEMPTS + 1):
            if time.monotonic() > deadline:
                res["detail"] = "time budget reached"
                break
            res["shortlist_tries"] = attempt
            await page.goto(link, wait_until="domcontentloaded", timeout=30000)
            await _settle(page, 500)
            sl = await _first_visible(page.get_by_text(SHORTLIST_BTN))
            if sl:
                try:
                    await sl.click(timeout=3000)
                    await page.wait_for_timeout(1200)
                except Exception:
                    pass
            await page.goto(SHORTLIST_URL, wait_until="domcontentloaded", timeout=30000)
            await _settle(page, 800)
            anchor = page.locator(f"a[href*='/{item['id']}/']").first
            if not await anchor.count():
                res["detail"] = "item not found on shortlist page (shortlisting may need a logged-in session)"
                continue
            found = await anchor.evaluate(_FIND_SHORTLIST_BTN_JS)
            if not found:
                res["detail"] = "no 'Add to cart' control next to the shortlisted item"
                continue
            try:
                await page.locator("[data-hw-target='1']").first.click(timeout=4000)
            except Exception as e:
                res["detail"] = f"shortlist add click failed: {type(e).__name__}"
                continue
            await page.wait_for_timeout(1500)
            if await _cart_contains(cart, item):
                res["status"] = "added_shortlist"
                return res
            res["detail"] = "added from shortlist but item not visible in cart"

        await debug_dump(cart, f"cart_{item['id']}", force=True)
        return res
    except Exception as e:
        res["detail"] = f"error: {type(e).__name__}: {e}"
        logging.error(f"[cart] {item['id']}: {e}")
        return res
    finally:
        await ctx.close()
