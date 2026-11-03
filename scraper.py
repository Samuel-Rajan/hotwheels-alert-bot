import asyncio
import hashlib
import logging
import os
import re
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
# Delivery location (Chennai) + pincode used for stock availability.
LOCATION = {"latitude": 13.0827, "longitude": 80.2707}
PINCODE = os.getenv("PINCODE") or "600001"  # set the PINCODE secret in GitHub for your area

# Which platforms to scan. GitHub Actions' US IPs are blocked by BigBasket, Blinkit,
# Instamart and Zepto (see debug output), so only FirstCry is on by default.
# On a machine with an Indian IP you can run:  PLATFORMS=all python main.py
ENABLED = [p.strip().lower() for p in os.getenv("PLATFORMS", "firstcry").split(",") if p.strip()]

DEBUG_DIR = Path("debug")
PLATFORM_TIMEOUT = 150  # seconds per platform

PLATFORMS = [
    {
        "name": "FirstCry",
        # Brand listing pages (server-rendered, only Hot Wheels items) instead of fuzzy search.
        "urls": [
            "https://www.firstcry.com/hot-wheels/5/0/113",
            "https://www.firstcry.com/hot-wheels-by-mattel/toy-cars-trains-and-vehicles/5/94/1011464",
        ],
        "base": "https://www.firstcry.com",
        "id_re": r"/([0-9a-zA-Z]{5,})/product-detail",
        "link_sel": "product-detail",
        "set_pincode": True,
    },
    {
        "name": "BigBasket",
        "urls": ["https://www.bigbasket.com/ps/?q=hot%20wheels&nc=as"],
        "base": "https://www.bigbasket.com",
        "id_re": r"/pd/(\d+)",
        "link_sel": "/pd/",
    },
    {
        "name": "Blinkit",
        "urls": ["https://blinkit.com/s/?q=hot%20wheels"],
        "base": "https://blinkit.com",
        "id_re": r"/prid/(\d+)",
        "link_sel": "/prid/",
    },
    {
        "name": "Instamart",
        "urls": ["https://www.swiggy.com/instamart/search?custom_back=true&query=hot%20wheels"],
        "base": "https://www.swiggy.com",
        "id_re": r"/item/(\w+)",
        "link_sel": "/item/",
    },
    {
        "name": "Zepto",
        "urls": ["https://www.zeptonow.com/search?query=hot%20wheels"],
        "base": "https://www.zeptonow.com",
        "id_re": r"/pvid/([\w-]+)",
        "link_sel": "/pn/",
    },
]

KEY_RE = r"hot.?wheels"  # matches "hot wheels", "hot-wheels", "hotwheels"

OOS_MARKERS = ["out of stock", "sold out", "notify me", "currently unavailable", "notify"]
BLOCK_MARKERS = ["access denied", "captcha", "unusual traffic", "forbidden", "request blocked",
                 "are you a robot", "you have been blocked", "we'll be back shortly"]
NO_RESULT_MARKERS = ["no results", "no products found", "couldn't find", "could not find",
                     "no items found", "0 results", "0 items"]
NOISE_LINE = re.compile(r"^(ad|bestseller|new|international|sponsored|get it by.*|\d+% off|club price.*|\(.*\)|[\d.,\s₹]+)$", re.I)

# Class-name independent extractor. A "card" is any product link (matched by URL pattern) or any
# price-bearing block, whose text / image alt / link URL mentions Hot Wheels.
EXTRACT_JS = """
({linkSel, keyRe}) => {
  const kre = new RegExp(keyRe, "i");
  const PRICE = /₹|Rs\\.?\\s?\\d|INR|Club Price/;
  const cards = new Set();

  // Pass 1: text nodes mentioning the keyword -> nearest ancestor with a price
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

  // Pass 2: product links; keyword may be in the text, image alt, or the URL path itself
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
    const hay = (el.innerText || "") + " " + alt + " " + (a.getAttribute("title") || "") + " " + href;
    if (kre.test(hay)) cards.add(el);
  });

  const arr = [...cards];
  const inner = arr.filter(c => !arr.some(o => o !== c && c.contains(o)));
  return inner.map(c => {
    const a = c.tagName === "A" ? c : (c.querySelector("a[href]") || c.closest("a[href]"));
    const img = c.querySelector("img");
    return {
      text: c.innerText || "",
      alt: img ? (img.alt || "") : "",
      href: a ? a.getAttribute("href") : "",
      img: img ? (img.currentSrc || img.src || img.getAttribute("data-src") || "") : ""
    };
  });
}
"""


def _parse_card(raw: dict, cfg: dict):
    text = raw.get("text", "")
    alt = raw.get("alt", "")
    href = raw.get("href") or ""
    low = text.lower()

    if not re.search(KEY_RE, f"{text} {alt} {href}", re.I):
        return None
    if any(m in low for m in OOS_MARKERS):
        return None

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    clean = [l for l in lines if not NOISE_LINE.match(l)]
    title = next((l for l in clean if re.search(KEY_RE, l, re.I)), None)
    if not title:
        title = alt.strip() or (clean[0] if clean else "Hot Wheels")
    title = title[:120]

    price = "Check App"
    price_match = re.search(r"(₹|Rs\.?)\s?[\d,]+(\.\d+)?", text)
    if price_match:
        price = price_match.group(0)
    else:
        start = lines.index(title) + 1 if title in lines else 0
        for l in lines[start:]:
            m = re.match(r"^(\d[\d,]*(?:\.\d+)?)(\s|$)", l)
            if m:
                price = f"₹{m.group(1)}"
                break

    if href and not href.startswith(("javascript:", "#")):
        link = urljoin(cfg["base"], href)
    else:
        link = cfg["urls"][0]

    id_match = re.search(cfg["id_re"], href)
    prod_id = id_match.group(1) if id_match else "t_" + hashlib.sha1(title.lower().encode()).hexdigest()[:12]

    img = raw.get("img") or ""
    if img.startswith("//"):
        img = "https:" + img
    if not img.startswith("http"):
        img = ""

    return {"platform": cfg["name"], "id": prod_id, "title": title, "price": price, "link": link, "image": img}


async def _debug_dump(page, name: str, force: bool = False):
    if not force and os.getenv("DEBUG_DUMP") != "1":
        return
    DEBUG_DIR.mkdir(exist_ok=True)
    try:
        await page.screenshot(path=str(DEBUG_DIR / f"{name}.png"), full_page=False)
        text = await page.inner_text("body")
        (DEBUG_DIR / f"{name}.txt").write_text(text[:6000], encoding="utf-8")
    except Exception as e:
        logging.warning(f"[{name}] debug dump failed: {e}")


async def _set_pincode(page, name: str) -> bool:
    """Best-effort: open FirstCry's location picker and enter a pincode."""
    try:
        trigger = page.get_by_text(re.compile(r"select a location|deliver to|enter pincode", re.I)).first
        await trigger.click(timeout=4000)
        inp = page.locator("input[placeholder*='pin' i], input[id*='pin' i], input[name*='pin' i], input[type='tel']").first
        await inp.fill(PINCODE, timeout=4000)
        await inp.press("Enter")
        try:
            await page.get_by_role("button", name=re.compile(r"apply|check|submit|update", re.I)).first.click(timeout=2000)
        except Exception:
            pass
        await page.wait_for_timeout(3000)
        logging.info(f"[{name}] Pincode submitted")
        return True
    except Exception as e:
        logging.warning(f"[{name}] Could not set pincode: {e}")
        return False


async def _load_all(page, link_sel: str, max_rounds: int = 25):
    """Scroll until the number of Hot Wheels mentions on the page stops growing."""
    js = "(re) => (document.body.innerText.match(new RegExp(re, 'gi')) || []).length"
    last, stable = 0, 0
    for _ in range(max_rounds):
        cnt = await page.evaluate(js, KEY_RE)
        if cnt == last:
            stable += 1
            if stable >= 3:
                break
        else:
            stable, last = 0, cnt
        await page.mouse.wheel(0, 3000)
        await page.wait_for_timeout(1000)
    logging.info(f"Scrolled; ~{last} Hot Wheels mentions loaded")


async def _scrape_platform(browser, device: dict, cfg: dict):
    """Returns (platform_name, items, ok). ok=False means 'could not get a trustworthy scan'."""
    name = cfg["name"]
    ctx = await browser.new_context(
        **device,
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        geolocation=LOCATION,
        permissions=["geolocation"],
    )
    page = await ctx.new_page()
    items, all_ok = [], True
    try:
        for idx, url in enumerate(cfg["urls"]):
            logging.info(f"[{name}] Loading {url}")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass

            if idx == 0 and cfg.get("set_pincode"):
                if await _set_pincode(page, name):
                    await page.reload(wait_until="domcontentloaded")
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass

            await _load_all(page, cfg["link_sel"])

            body = (await page.inner_text("body")).lower()
            if len(body) < 300 or any(m in body for m in BLOCK_MARKERS):
                logging.warning(f"[{name}] {url} looks blocked/empty (len={len(body)})")
                await _debug_dump(page, f"{name}_{idx}", force=True)
                all_ok = False
                continue

            raw_cards = await page.evaluate(EXTRACT_JS, {"linkSel": cfg["link_sel"], "keyRe": KEY_RE})
            parsed = [i for i in (_parse_card(r, cfg) for r in raw_cards) if i]
            logging.info(f"[{name}] page {idx}: {len(raw_cards)} Hot Wheels cards, {len(parsed)} in stock")
            await _debug_dump(page, f"{name}_{idx}")   # always saved so you can verify what the bot saw
            items.extend(parsed)

            if not raw_cards and not any(m in body for m in NO_RESULT_MARKERS):
                await _debug_dump(page, f"{name}_{idx}", force=True)
                logging.warning(f"[{name}] page {idx}: 0 cards and no 'no results' message -> scan failed")
                all_ok = False

        return name, items, all_ok
    except Exception as e:
        logging.error(f"[{name}] Error: {e}")
        await _debug_dump(page, f"{name}_error", force=True)
        return name, [], False
    finally:
        await ctx.close()


async def _guarded(browser, device, cfg):
    try:
        return await asyncio.wait_for(_scrape_platform(browser, device, cfg), timeout=PLATFORM_TIMEOUT)
    except asyncio.TimeoutError:
        logging.error(f"[{cfg['name']}] Timed out")
        return cfg["name"], [], False


async def fetch_all_stores():
    """Returns (items_in_stock, set_of_platforms_scanned_successfully)."""
    selected = [c for c in PLATFORMS if "all" in ENABLED or c["name"].lower() in ENABLED]
    logging.info(f"Platforms enabled: {[c['name'] for c in selected]}")

    all_items, ok_platforms = [], set()
    async with async_playwright() as p:
        device = dict(p.devices["iPhone 13"])
        browser = await p.chromium.launch(headless=True)
        try:
            results = await asyncio.gather(*[_guarded(browser, device, cfg) for cfg in selected])
        finally:
            await browser.close()

    for name, items, ok in results:
        if ok:
            ok_platforms.add(name)
        all_items.extend(items)

    unique = {}
    for it in all_items:
        unique.setdefault(f"{it['platform']}_{it['id']}", it)
    return list(unique.values()), ok_platforms
