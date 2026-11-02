import logging
import os
import asyncio
import re
from playwright.async_api import async_playwright

def extract_product_id(url):
    """Extracts a clean, stable product ID from a URL to prevent duplicate/false triggers."""
    if not url:
        return ""
    # Look for common product path patterns (e.g., /p/, /prid/, /dp/, numeric IDs)
    match = re.search(r'/(?:p|prid|dp|ip|product)/([a-zA-Z0-9\-_]+)', url)
    if match:
        return match.group(1)
    # Fallback to last meaningful path segment if numeric or alphanumeric code exists
    segments = [s for s in url.split('/') if s]
    if segments:
        return segments[-1]
    return url

async def fetch_store_with_fallbacks(page, store_name, url, keyword="hot wheels"):
    """
    Precision scraper that targets genuine product containers and excludes 
    category links, banners, and search filters.
    """
    in_stock_items = []
    try:
        logging.info(f"[{store_name}] Navigating to URL: {url}")
        response = await page.goto(url, wait_until="domcontentloaded", timeout=35000)
        
        if response and response.status >= 400:
            logging.warning(f"[{store_name}] HTTP status {response.status} encountered.")

        # Simulate scrolling to load lazy items
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await asyncio.sleep(1.5)

        await page.wait_for_timeout(3000)

        # Target specific product card wrappers rather than random page links
        card_selectors = [
            "div[class*='product']", "div[class*='item-card']", 
            "div[class*='card']", "li[class*='product']", ".prod-item"
        ]
        
        cards = []
        for sel in card_selectors:
            cards = await page.locator(sel).all()
            if cards:
                break

        # Fallback if specific cards aren't found
        if not cards:
            cards = await page.locator("a[href]").all()

        seen_ids = set()

        for card in cards:
            try:
                text = await card.inner_text()
                if not text or keyword not in text.lower():
                    continue

                # Exclude category/search/banner text blocks
                text_lower = text.lower()
                if any(excluded in text_lower for excluded in ["category", "filter", "sort by", "sponsored ad"]):
                    continue

                # Find the product link
                href = ""
                tag_name = await card.evaluate("el => el.tagName.toLowerCase()")
                if tag_name == "a":
                    href = await card.get_attribute("href")
                else:
                    link_elem = card.locator("a[href]").first
                    if await link_elem.count() > 0:
                        href = await link_elem.get_attribute("href")

                if not href or href == "#":
                    continue

                # Ignore search result links or pagination links that aren't individual products
                if "/search?" in href or "/s?" in href or "query=" in href:
                    continue

                prod_id = extract_product_id(href)
                if not prod_id or prod_id in seen_ids:
                    continue

                seen_ids.add(prod_id)

                # Clean title
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                title = lines[0] if lines else "Hot Wheels Product"
                if len(title) < 5:
                    continue

                # Extract price if visible
                price_str = "Check App"
                for line in lines:
                    if "₹" in line or "INR" in line:
                        price_str = line
                        break

                in_stock_items.append({
                    "platform": store_name,
                    "id": prod_id,
                    "title": title,
                    "price": price_str,
                    "mrp": "",
                    "image": "",
                    "link": href
                })
            except Exception:
                continue

        if not in_stock_items:
            os.makedirs("debug_logs", exist_ok=True)
            await page.screenshot(path=f"debug_logs/{store_name.lower()}_empty.png")

    except Exception as e:
        logging.error(f"[{store_name}] Exception during fetch: {e}")

    return in_stock_items

async def fetch_all_stores():
    """Asynchronously scrapes Indian quick-commerce inventories with strict product filtering."""
    all_in_stock = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1280,800"
            ]
        )
        
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            permissions=["geolocation"],
            geolocation={"latitude": 13.0827, "longitude": 80.2707} # Chennai, India
        )

        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()

        # --- 1. FIRSTCRY ---
        firstcry_items = await fetch_store_with_fallbacks(page, "FirstCry", "https://www.firstcry.com/search?q=hot%20wheels")
        for item in firstcry_items:
            if item["link"].startswith("//"):
                item["link"] = f"https:{item['link']}"
            elif item["link"].startswith("/"):
                item["link"] = f"https://www.firstcry.com{item['link']}"
        all_in_stock.extend(firstcry_items)

        # --- 2. BLINKIT ---
        blinkit_items = await fetch_store_with_fallbacks(page, "Blinkit", "https://blinkit.com/s/?q=hot%20wheels")
        for item in blinkit_items:
            if item["link"].startswith("/"):
                item["link"] = f"https://blinkit.com{item['link']}"
        all_in_stock.extend(blinkit_items)

        # --- 3. ZEPTO ---
        zepto_items = await fetch_store_with_fallbacks(page, "Zepto", "https://www.zeptonow.com/search?query=hot%20wheels")
        for item in zepto_items:
            if item["link"].startswith("/"):
                item["link"] = f"https://www.zeptonow.com{item['link']}"
        all_in_stock.extend(zepto_items)

        await browser.close()
        
    return all_in_stock
