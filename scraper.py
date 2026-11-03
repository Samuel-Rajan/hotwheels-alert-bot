import asyncio
import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright

# Delivery location used for the quick-commerce sites (Chennai)
LOCATION = {"latitude": 13.0827, "longitude": 80.2707}

DEBUG_DIR = Path("debug")

PLATFORMS = [
    {
        "name": "FirstCry",
        "url": "https://www.firstcry.com/search?q=hot%20wheels&sort=newness",
        "base": "https://www.firstcry.com",
        "id_re": r"/(\d{5,})/product-detail",
    },
    {
        "name": "BigBasket",
        "url": "https://www.bigbasket.com/ps/?q=hot%20wheels&nc=as",
        "base": "https://www.bigbasket.com",
        "id_re": r"/pd/(\d+)",
    },
    {
        "name": "Blinkit",
        "url": "https://blinkit.com/s/?q=hot%20wheels",
        "base": "https://blinkit.com",
        "id_re": r"/prid/(\d+)",
    },
    {
        "name": "Instamart",
        "url": "https://www.swiggy.com/instamart/search?custom_back=true&query=hot%20wheels",
        "base": "https://www.swiggy.com",
        "id_re": r"/item/(\w+)",
    },
    {
        "name": "Zepto",
        "url": "https://www.zeptonow.com/search?query=hot%20wheels",
        "base": "https://www.zeptonow.com",
        "id_re": r"/pvid/([\w-]+)",
    },
]

OOS_MARKERS = ["out of stock", "sold out", "notify me", "currently unavailable", "notify"]
BLOCK_MARKERS = ["access denied", "captcha", "unusual traffic", "forbidden", "request blocked", "are you a robot"]

PLATFORM_TIMEOUT = 70  # seconds per platform

# Class-name independent extractor: finds every text node mentioning "hot wheels",
# climbs up to the nearest ancestor that also shows a price, and treats it as a product card.
EXTRACT_JS = """
() => {
  const KEY = "hot wheels";
  const PRICE = /₹|Rs\\.?\\s?\\d|INR/;
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const cards = new Set();
  let n;
  while ((n = walker.nextNode())) {
    if (!n.nodeValue.toLowerCase().includes(KEY)) continue;
    let el = n.parentElement;
    for (let i = 0; el && i < 8; i++, el = el.parentElement) {
      const t = el.innerText || "";
      if (t.length > 600) break;
      if (PRICE.test(t)) { cards.add(el); break; }
    }
  }
  const arr = [...cards];
  const inner = arr.filter(c => !arr.some(o => o !== c && c.contains(o)));
  return inner.map(c => {
    const a = c.tagName === "A" ? c : (c.querySelector("a[href]") || c.closest("a[href]"));
    const img = c.querySelector("img");
    return {
      text: c.innerText || "",
      href: a ? a.getAttribute("href") : "",
      img: img ? (img.currentSrc || img.src || img.getAttribute("data-src") || "") : ""
    };
  });
}
"""


def _parse_card(raw: dict, cfg: dict):
    text = raw.get("text", "")
    low = text.lower()
    if "hot wheels" not in low:
        return None
    if any(m in low for m in OOS_MARKERS):
        return None

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    title = next((l for l in lines if "hot wheels" in l.lower()), lines[0])[:120]

    price_match = re.search(r"(₹|Rs\.?)\s?[\d,]+(\.\d+)?", text)
    price = price_match.group(0) if price_match else "Check App"

    href = raw.get("href") or ""
    if href and not href.startswith(("javascript:", "#")):
        link = urljoin(cfg["base"], href)
    else:
        link = cfg["url"]

    id_match = re.search(cfg["id_re"], href or "")
    if id_match:
        prod_id = id_match.group(1)
    else:
        prod_id = "t_" + hashlib.sha1(title.lower().encode()).hexdigest()[:12]

    img = raw.get("img") or ""
    if img.startswith("//"):
        img = "https:" + img
    if not img.startswith("http"):
        img = ""

    return {
        "platform": cfg["name"],
        "id": prod_id,
        "title": title,
        "price": price,
        "link": link,
        "image": img,
    }


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
    try:
        logging.info(f"[{name}] Loading {cfg['url']}")
        await page.goto(cfg["url"], timeout=30000, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # scroll to trigger lazy loading
        for _ in range(4):
            await page.mouse.wheel(0, 1800)
            await page.wait_for_timeout(800)

        body = (await page.inner_text("body")).lower()
        if len(body) < 300 or any(m in body for m in BLOCK_MARKERS):
            logging.warning(f"[{name}] Page looks blocked/empty (len={len(body)})")
            await _debug_dump(page, name)
            return name, [], False

        raw_cards = await page.evaluate(EXTRACT_JS)
        items = [i for i in (_parse_card(r, cfg) for r in raw_cards) if i]
        logging.info(f"[{name}] {len(raw_cards)} Hot Wheels cards, {len(items)} in stock")
        if not raw_cards:
            await _debug_dump(page, name)
        return name, items, True
    except Exception as e:
        logging.error(f"[{name}] Error: {e}")
        try:
            await _debug_dump(page, name)
        except Exception:
            pass
        return name, [], False
    finally:
        await ctx.close()


async def _debug_dump(page, name: str):
    DEBUG_DIR.mkdir(exist_ok=True)
    await page.screenshot(path=str(DEBUG_DIR / f"{name}.png"), full_page=False)


async def _guarded(browser, device, cfg):
    try:
        return await asyncio.wait_for(_scrape_platform(browser, device, cfg), timeout=PLATFORM_TIMEOUT)
    except asyncio.TimeoutError:
        logging.error(f"[{cfg['name']}] Timed out")
        return cfg["name"], [], False


async def fetch_all_stores():
    """Returns (items_in_stock, set_of_platforms_scanned_successfully)."""
    all_items, ok_platforms = [], set()
    async with async_playwright() as p:
        device = dict(p.devices["iPhone 13"])
        browser = await p.chromium.launch(headless=True)
        try:
            results = await asyncio.gather(*[_guarded(browser, device, cfg) for cfg in PLATFORMS])
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
