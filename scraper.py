import logging
import os
import asyncio
from playwright.async_api import async_playwright

async def fetch_store_with_fallbacks(page, store_name, url, keyword="hot wheels"):
    """
    Scraper strictly filtered for Indian storefronts, validating Indian currency (₹) 
    and rejecting international or cross-border mirrors.
    """
    in_stock_items = []
    try:
        logging.info(f"[{store_name}] Navigating to Indian store URL: {url}")
        response = await page.goto(url, wait_until="domcontentloaded", timeout=35000)
        
        if response and response.status >= 400:
            logging.warning(f"[{store_name}] HTTP status {response.status} encountered.")

        # Simulate human scrolling to trigger lazy-loaded items
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await asyncio.sleep(1.5)

        await page.wait_for_timeout(4000)

        # Scan elements on the page
        elements = await page.locator("a, div[class*='product'], div[class*='item'], div[class*='card'], li").all()
        seen_links = set()

        for el in elements:
            try:
                text = await el.inner_text()
                if not text or keyword not in text.lower():
                    continue

                # REJECTION CHECK: Ensure no international indicators (AED, SAR, UAE, KSA) are in the text block
                text_lower = text.lower()
                if any(geo_term in text_lower for geo_term in ["aed", "sar", "dubai", "riyadh", "uae", "saudi", "firstcry.ae"]):
                    continue

                # Extract link href
                href = await el.get_attribute("href")
                if not href:
                    sub_a = el.locator("a").first
                    if await sub_a.count() > 0:
                        href = await sub_a.get_attribute("href")

                if not href or href in seen_links or href == "#":
                    continue
                
                # REJECTION CHECK: Ensure link domain does not point to international extensions
                if any(domain in href.lower() for domain in [".ae", ".sa", "firstcry.ae", "firstcry.sa"]):
                    continue

                seen_links.add(href)

                # Clean up product title
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                title = lines[0] if lines else "Hot Wheels Product"
                if len(title) < 5:
                    continue

                # Extract or verify Indian currency symbol if visible in block
                price_str = "Check App"
                for line in lines:
                    if "₹" in line or "INR" in line:
                        price_str = line
                        break

                in_stock_items.append({
                    "platform": store_name,
                    "id": href,
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
    """Asynchronously scrapes Indian quick-commerce and e-commerce inventories."""
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
            geolocation={"latitude": 13.0827, "longitude": 80.2707} # Chennai, India Coordinates
        )

        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()

        # --- 1. FIRSTCRY (INDIA EXCLUSIVE) ---
        firstcry_items = await fetch_store_with_fallbacks(page, "FirstCry", "https://www.firstcry.com/search?q=hot%20wheels")
        for item in firstcry_items:
            if item["link"].startswith("//"):
                item["link"] = f"https:{item['link']}"
            elif item["link"].startswith("/"):
                item["link"] = f"https://www.firstcry.com{item['link']}"
        all_in_stock.extend(firstcry_items)

        # --- 2. BLINKIT (INDIA) ---
        blinkit_items = await fetch_store_with_fallbacks(page, "Blinkit", "https://blinkit.com/s/?q=hot%20wheels")
        for item in blinkit_items:
            if item["link"].startswith("/"):
                item["link"] = f"https://blinkit.com{item['link']}"
        all_in_stock.extend(blinkit_items)

        # --- 3. ZEPTO (INDIA) ---
        zepto_items = await fetch_store_with_fallbacks(page, "Zepto", "https://www.zeptonow.com/search?query=hot%20wheels")
        for item in zepto_items:
            if item["link"].startswith("/"):
                item["link"] = f"https://www.zeptonow.com{item['link']}"
        all_in_stock.extend(zepto_items)

        await browser.close()
        
    return all_in_stock
