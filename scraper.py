import asyncio
import hashlib
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
LOCATION = {"latitude": 13.0827, "longitude": 80.2707}          # Chennai
PINCODE = os.getenv("PINCODE") or "600001"                      # set the PINCODE secret for your area

# FirstCry only by default. The quick-commerce apps block GitHub's US IPs.
# On a machine with an Indian IP you can run:  PLATFORMS=all python main.py
ENABLED = [p.strip().lower() for p in os.getenv("PLATFORMS", "firstcry").split(",") if p.strip()]

DEBUG_DIR = Path("debug")
PLATFORM_TIMEOUT = 200  # seconds per platform

# FirstCry serves a *mobile* layout (no real product links) to phone user-agents and a
# full desktop layout (real /product-detail links) to desktop browsers. We use desktop.
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DESKTOP_CTX = dict(
    user_agent=DESKTOP_UA,
    viewport={"width": 1440, "height": 900},
    locale="en-IN",
    timezone_id="Asia/Kolkata",
    geolocation=LOCATION,
    permissions=["geolocation"],
)

PLATFORMS = [
    {
        "name": "FirstCry",
        "kind": "firstcry",
        "urls": [
            "https://www.firstcry.com/hot-wheels/5/0/113",
            "https://www.firstcry.com/hot-wheels-by-mattel/toy-cars-trains-and-vehicles/5/94/1011464",
        ],
        "base": "https://www.firstcry.com",
    },
    {"name": "BigBasket", "kind": "generic", "urls": ["https://www.bigbasket.com/ps/?q=hot%20wheels&nc=as"],
     "base": "https://www.bigbasket.com", "id_re": r"/pd/(\d+)", "link_sel": "/pd/"},
    {"name": "Blinkit", "kind": "generic", "urls": ["https://blinkit.com/s/?q=hot%20wheels"],
     "base": "https://blinkit.com", "id_re": r"/prid/(\d+)", "link_sel": "/prid/"},
    {"name": "Instamart", "kind": "generic",
     "urls": ["https://www.swiggy.com/instamart/search?custom_back=true&query=hot%20wheels"],
     "base": "https://www.swiggy.com", "id_re": r"/item/(\w+)", "link_sel": "/item/"},
    {"name": "Zepto", "kind": "generic", "urls": ["https://www.zeptonow.com/search?query=hot%20wheels"],
     "base": "https://www.zeptonow.com", "id_re": r"/pvid/([\w-]+)", "link_sel": "/pn/"},
]

KEY_RE = r"hot.?wheels"
OOS_MARKERS = ["out of stock", "sold out", "notify me", "currently unavailable", "notify"]
BLOCK_MARKERS = ["access denied", "captcha", "unusual traffic", "forbidden", "request blocked",
                 "are you a robot", "you have been blocked", "we'll be back shortly"]
NO_RESULT_MARKERS = ["no results", "no products found", "couldn't find", "could not find",
                     "no items found", "0 results", "0 items"]


@asynccontextmanager
async def launch_browser():
    """One shared Chromium for scanning, stock verification and cart actions."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            yield p, browser
        finally:
            await browser.close()


async def debug_dump(page, name: str, force: bool = False):
    if not force and os.getenv("DEBUG_DUMP") != "1":
        return
    DEBUG_DIR.mkdir(exist_ok=True)
    try:
        await page.screenshot(path=str(DEBUG_DIR / f"{name}.png"), full_page=False)
        text = await page.inner_text("body")
        (DEBUG_DIR / f"{name}.txt").write_text(text[:6000], encoding="utf-8")
    except Exception as e:
        logging.warning(f"[{name}] debug dump failed: {e}")


# ===========================================================================
# FirstCry (desktop layout, real product links)
# ===========================================================================
# A product card = the largest ancestor of a product's image link that contains no other product.
FC_EXTRACT_JS = r"""
() => {
  const PID = /\/(\d+)\/product-detail/;
  const HW = /hot.?wheels/i;
  const pidOf = a => { const m = PID.exec(a.getAttribute("href") || ""); return m ? m[1] : null; };
  const links = [...document.querySelectorAll("a[href*='/product-detail']")];
  const imgLinks = links.filter(a => pidOf(a) && a.querySelector("img") && HW.test(a.getAttribute("href")));
  const imgSet = new Set(imgLinks);
  const countPids = el => {
    const s = new Set();
    el.querySelectorAll("a[href*='/product-detail']").forEach(x => { if (imgSet.has(x)) s.add(pidOf(x)); });
    return s.size;
  };
  const seen = new Set(), out = [];
  for (const a of imgLinks) {
    const pid = pidOf(a);
    if (seen.has(pid)) continue;
    seen.add(pid);
    let card = a;
    while (card.parentElement && card.parentElement !== document.body &&
           countPids(card.parentElement) <= 1 &&
           (card.parentElement.innerText || "").length < 1500) {
      card = card.parentElement;
    }
    const img = a.querySelector("img");
    const tl = [...card.querySelectorAll("a[href*='/product-detail']")]
      .find(x => pidOf(x) === pid && x.getAttribute("title"));
    out.push({
      pid: pid,
      href: a.getAttribute("href"),
      title: tl ? tl.getAttribute("title") : ((img && (img.getAttribute("title") || img.alt)) || ""),
      text: card.innerText || "",
      img: img ? (img.currentSrc || img.src || img.getAttribute("data-src") || "") : ""
    });
  }
  return out;
}
"""

FC_COUNT_JS = r"""
() => {
  const s = new Set();
  document.querySelectorAll("a[href*='/product-detail']").forEach(a => {
    const h = a.getAttribute("href") || "";
    const m = /\/(\d+)\/product-detail/.exec(h);
    if (m && a.querySelector("img") && /hot.?wheels/i.test(h)) s.add(m[1]);
  });
  return s.size;
}
"""

PRICE_LINE = re.compile(
    r"₹?\s*\d[\d,]*(?:\.\d+)?(?:\s+₹?\d[\d,]*(?:\.\d+)?)?(?:\s*\(.*?\))?", re.I
)
IN_STOCK_RE = re.compile(r"add(?:ed)? to cart|go to cart|buy now", re.I)


def parse_fc_card(raw: dict, cfg: dict):
    """Returns an item dict if the card is in stock, else None."""
    text = raw.get("text", "")
    low = text.lower()
    if not IN_STOCK_RE.search(low):
        return None
    if any(m in low for m in OOS_MARKERS):
        return None

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    title = (raw.get("title") or "").strip()
    if not title:
        title = next((l for l in lines if re.search(KEY_RE, l, re.I)), lines[0] if lines else "Hot Wheels")
    title = re.sub(r"\s+", " ", title)[:150]

    price = "Check price"
    for l in lines:
        if PRICE_LINE.fullmatch(l):
            m = re.search(r"\d[\d,]*(?:\.\d+)?", l)
            if m:
                price = f"₹{m.group(0)}"
                break

    if price == "Check price":
        # Text may come out inline (no line breaks): the price sits right before "Club Price".
        m = re.search(r"(\d[\d,]*(?:\.\d+)?)(?:\s+\d[\d,]*(?:\.\d+)?)?(?:\s*\([^)]*\))?\s*Club Price", text, re.I)
        if m:
            price = f"₹{m.group(1)}"

    href = raw.get("href") or ""
    link = urljoin(cfg["base"], href) if href else cfg["urls"][0]

    img = raw.get("img") or ""
    if img.startswith("//"):
        img = "https:" + img
    if not img.startswith("http"):
        img = ""

    return {"platform": cfg["name"], "id": str(raw["pid"]), "title": title,
            "price": price, "link": link, "image": img}


async def _set_pincode(page, name: str) -> bool:
    """Best effort: enter the delivery pincode so availability matches your area."""
    try:
        box = page.locator("input[placeholder*='incode' i]:visible").first
        if not await box.count():
            return False
        await box.fill(PINCODE, timeout=4000)
        btn = page.locator("button:visible, a:visible, div[role=button]:visible").filter(
            has_text=re.compile(r"^\s*(apply|check)\s*$", re.I)).first
        try:
            await btn.click(timeout=3000)
        except Exception:
            await box.press("Enter")
        await page.wait_for_timeout(3000)
        logging.info(f"[{name}] Pincode submitted")
        return True
    except Exception as e:
        logging.warning(f"[{name}] Could not set pincode: {e}")
        return False


async def _load_all_fc(page, name: str, max_rounds: int = 60) -> int:
    """Click 'Show More Products' / scroll until the number of products stops growing."""
    last, stable = -1, 0
    for _ in range(max_rounds):
        cnt = await page.evaluate(FC_COUNT_JS)
        if cnt == last:
            stable += 1
            if stable >= 3:
                break
        else:
            stable, last = 0, cnt
        clicked = False
        try:
            more = page.get_by_text(re.compile(r"^\s*show more products\s*$", re.I)).first
            if await more.count() and await more.is_visible():
                await more.scroll_into_view_if_needed(timeout=2000)
                await more.click(timeout=3000)
                clicked = True
        except Exception:
            pass
        if not clicked:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1200)
    logging.info(f"[{name}] Loaded {last} Hot Wheels products on page")
    return last


async def _scrape_firstcry(browser, cfg: dict):
    name = cfg["name"]
    ctx = await browser.new_context(**DESKTOP_CTX)
    page = await ctx.new_page()
    found, all_ok = {}, True
    try:
        for idx, url in enumerate(cfg["urls"]):
            logging.info(f"[{name}] Loading {url}")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass

            if idx == 0 and await _set_pincode(page, name):
                await page.reload(wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

            await _load_all_fc(page, name)
            body = (await page.inner_text("body")).lower()
            if len(body) < 300 or any(m in body for m in BLOCK_MARKERS):
                logging.warning(f"[{name}] page {idx} looks blocked/empty (len={len(body)})")
                await debug_dump(page, f"{name}_{idx}", force=True)
                all_ok = False
                continue

            raw_cards = await page.evaluate(FC_EXTRACT_JS)
            parsed = [i for i in (parse_fc_card(r, cfg) for r in raw_cards) if i]
            logging.info(f"[{name}] page {idx}: {len(raw_cards)} products, {len(parsed)} in stock")
            await debug_dump(page, f"{name}_{idx}")
            for it in parsed:
                found.setdefault(it["id"], it)

            if not raw_cards and not any(m in body for m in NO_RESULT_MARKERS):
                logging.warning(f"[{name}] page {idx}: 0 products and no 'no results' message -> scan failed")
                await debug_dump(page, f"{name}_{idx}", force=True)
                all_ok = False

        return name, list(found.values()), all_ok
    except Exception as e:
        logging.error(f"[{name}] Error: {e}")
        await debug_dump(page, f"{name}_error", force=True)
        return name, [], False
    finally:
        await ctx.close()


# ===========================================================================
# Generic scraper for the quick-commerce apps (only useful from an Indian IP)
# ===========================================================================
GENERIC_EXTRACT_JS = r"""
({linkSel, keyRe}) => {
  const kre = new RegExp(keyRe, "i");
  const PRICE = /₹|Rs\.?\s?\d|INR|Club Price/;
  const cards = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    if (!kre.test(n.nodeValue)) continue;
    let el = n.parentElement;
    for (let i = 0; el && i < 8; i++, el = el.parentElement) {
      const t = el.innerText || "";
      if (t.length > 600) break;
      if (PRICE.test(t)) { cards.add(el); break; }
    }
  }
  document.querySelectorAll("a[href]").forEach(a => {
    const href = a.getAttribute("href") || "";
    if (!href.includes(linkSel)) return;
    let el = a;
    for (let i = 0; i < 4 && el.parentElement; i++) {
      const t = el.innerText || "";
      if (PRICE.test(t) && t.length > 20) break;
      if ((el.parentElement.innerText || "").length > 600) break;
      el = el.parentElement;
    }
    const alt = [...el.querySelectorAll("img")].map(i => (i.alt || "") + " " + (i.title || "")).join(" ");
    if (kre.test((el.innerText || "") + " " + alt + " " + href)) cards.add(el);
  });
  const arr = [...cards];
  const inner = arr.filter(c => !arr.some(o => o !== c && c.contains(o)));
  return inner.map(c => {
    const a = c.tagName === "A" ? c : (c.querySelector("a[href]") || c.closest("a[href]"));
    const img = c.querySelector("img");
    return {text: c.innerText || "", alt: img ? (img.alt || "") : "",
            href: a ? a.getAttribute("href") : "",
            img: img ? (img.currentSrc || img.src || img.getAttribute("data-src") || "") : ""};
  });
}
"""


def _parse_generic(raw: dict, cfg: dict):
    text, alt, href = raw.get("text", ""), raw.get("alt", ""), raw.get("href") or ""
    low = text.lower()
    if not re.search(KEY_RE, f"{text} {alt} {href}", re.I) or any(m in low for m in OOS_MARKERS):
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    title = next((l for l in lines if re.search(KEY_RE, l, re.I)), alt.strip() or "Hot Wheels")[:120]
    pm = re.search(r"(₹|Rs\.?)\s?[\d,]+(\.\d+)?", text)
    link = urljoin(cfg["base"], href) if href and not href.startswith(("javascript:", "#")) else cfg["urls"][0]
    idm = re.search(cfg["id_re"], href)
    pid = idm.group(1) if idm else "t_" + hashlib.sha1(title.lower().encode()).hexdigest()[:12]
    img = raw.get("img") or ""
    img = "https:" + img if img.startswith("//") else img
    return {"platform": cfg["name"], "id": pid, "title": title,
            "price": pm.group(0) if pm else "Check App", "link": link,
            "image": img if img.startswith("http") else ""}


async def _scrape_generic(browser, device: dict, cfg: dict):
    name = cfg["name"]
    ctx = await browser.new_context(**device, locale="en-IN", timezone_id="Asia/Kolkata",
                                    geolocation=LOCATION, permissions=["geolocation"])
    page = await ctx.new_page()
    items, ok = [], True
    try:
        for idx, url in enumerate(cfg["urls"]):
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            for _ in range(5):
                await page.mouse.wheel(0, 1800)
                await page.wait_for_timeout(900)
            body = (await page.inner_text("body")).lower()
            if len(body) < 300 or any(m in body for m in BLOCK_MARKERS):
                logging.warning(f"[{name}] looks blocked/empty (len={len(body)})")
                await debug_dump(page, f"{name}_{idx}", force=True)
                ok = False
                continue
            raw = await page.evaluate(GENERIC_EXTRACT_JS, {"linkSel": cfg["link_sel"], "keyRe": KEY_RE})
            items += [i for i in (_parse_generic(r, cfg) for r in raw) if i]
            if not raw and not any(m in body for m in NO_RESULT_MARKERS):
                await debug_dump(page, f"{name}_{idx}", force=True)
                ok = False
        return name, items, ok
    except Exception as e:
        logging.error(f"[{name}] Error: {e}")
        return name, [], False
    finally:
        await ctx.close()


async def _guarded(coro, name):
    try:
        return await asyncio.wait_for(coro, timeout=PLATFORM_TIMEOUT)
    except asyncio.TimeoutError:
        logging.error(f"[{name}] Timed out")
        return name, [], False


async def fetch_all_stores(p, browser):
    """Returns (items_in_stock, set_of_platforms_scanned_successfully)."""
    selected = [c for c in PLATFORMS if "all" in ENABLED or c["name"].lower() in ENABLED]
    logging.info(f"Platforms enabled: {[c['name'] for c in selected]}")
    device = dict(p.devices["iPhone 13"])

    jobs = []
    for cfg in selected:
        coro = _scrape_firstcry(browser, cfg) if cfg["kind"] == "firstcry" else _scrape_generic(browser, device, cfg)
        jobs.append(_guarded(coro, cfg["name"]))
    results = await asyncio.gather(*jobs)

    all_items, ok_platforms = [], set()
    for name, items, ok in results:
        if ok:
            ok_platforms.add(name)
        all_items.extend(items)

    unique = {}
    for it in all_items:
        unique.setdefault(f"{it['platform']}_{it['id']}", it)
    return list(unique.values()), ok_platforms
