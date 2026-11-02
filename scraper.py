import logging
import os
from playwright.async_api import async_playwright

async def fetch_store_with_fallbacks(page, store_name, url, item_selectors, title_selectors, link_selectors, keyword="hot wheels"):
    """Generic robust fetcher with fallback selectors, stealth checks, and snapshot logging."""
    in_stock_items = []
    try:
        logging.info(f"Checking {store_name} via stealth browser session...")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(5000)  # Allow JS hydration & bypass initial challenges

        # Try fallback item selectors sequentially
        elements = []
        for selector in item_selectors:
            elements = await page.locator(selector).all()
            if elements:
                logging.info(f"[{store_name}] Successfully matched items using selector: {selector}")
                break

        # If zero items are found across all fallbacks, capture debug snapshot
        if not elements:
            logging.warning(f"[{store_name}] Zero items found. Capturing debug snapshot...")
            os.makedirs("debug_logs", exist_ok=True)
            html_content = await page.content()
            with open(f"debug_logs/{store_name.lower()}_fail.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            await page.screenshot(path=f"debug_logs/{store_name.lower()}_fail.png")
            return []

        for item in elements[:12]:
            try:
                # Extract title using fallback selectors
                title = ""
                for t_sel in title_selectors:
                    t_elem = item.locator(t_sel).first
                    if await t_elem.count() > 0:
                        text = await t_elem.inner_text()
                        if text.strip():
                            title = text.split("\n")[0].strip()
                            break
                
                if not title:
                    full_text = await item.inner_text()
                    title = full_text.split("\n")[0].strip() if full_text else ""

                if keyword not in title.lower():
                    continue

                # Extract link using fallback selectors
                link = ""
                for l_sel in link_selectors:
                    l_elem = item.locator(l_sel).first
                    if await l_elem.count() > 0:
                        href = await l_elem.get_attribute("href")
                        if href:
                            link = href
                            break

                if not link:
                    tag_name = await item.evaluate("el => el.tagName.toLowerCase()")
                    if tag_name == "a":
                        link = await item.get_attribute("href")

                in_stock_items.append({
                    "platform": store_name,
                    "id": link if link else title,
                    "title": title if title else "Hot Wheels Product",
                    "price": "Check App",
                    "mrp": "",
                    "image": "",
                    "link": link
                })
            except Exception:
                continue

    except Exception as e:
        logging.error(f"[{store_name}] Scraper execution error: {e}")
        try:
            os.makedirs("debug_logs", exist_ok=True)
            with open(f"debug_logs/{store_name.lower()}_exception.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
        except Exception:
            pass

    return in_stock_items

async def fetch_all_stores():
    """Asynchronously scrapes Hot Wheels stock across platforms using stealth evasion and fallbacks."""
    all_in_stock = []
    
    async with async_playwright() as p:
        # Launch Chromium with anti-detection flags to bypass cloud firewalls
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        
        # Configure browser context with Chennai geolocation and realistic headers
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            permissions=["geolocation"],
            geolocation={"latitude": 13.0827, "longitude": 80.2707}  # Chennai Hub Coordination
        )
        
        page = await context.new_page()

        # --- 1. BLINKIT ---
        blinkit_items = await fetch_store_with_fallbacks(
            page, 
            "Blinkit", 
            "https://blinkit.com/s/?q=hot%20wheels",
            item_selectors=["a[href*='/prid/']", "div[class*='product']", "a[class*='product']"],
            title_selectors=["div[class*='title']", "div", "span"],
            link_selectors=["a[href*='/prid/']", "a"]
        )
        for item in blinkit_items:
            if item["link"] and not item["link"].startswith("http"):
                item["link"] = f"https://blinkit.com{item['link']}" if item["link"].startswith("/") else "https://blinkit.com"
        all_in_stock.extend(blinkit_items)

        # --- 2. ZEPTO ---
        zepto_items = await fetch_store_with_fallbacks(
            page, 
            "Zepto", 
            "https://www.zeptonow.com/search?query=hot%20wheels",
            item_selectors=["a[data-testid='product-card']", "div[data-testid*='product']", "a[href*='/p/']"],
            title_selectors=["h4", "[class*='title']", "span"],
            link_selectors=["a"]
        )
        for item in zepto_items:
            if item["link"] and not item["link"].startswith("http"):
                item["link"] = f"https://www.zeptonow.com{item['link']}" if item["link"].startswith("/") else "https://www.zeptonow.com"
        all_in_stock.extend(zepto_items)

        # --- 3. FIRSTCRY ---
        firstcry_items = await fetch_store_with_fallbacks(
            page, 
            "FirstCry", 
            "https://www.firstcry.com/search?q=hot%20wheels",
            item_selectors=[".prod-item", "div[class*='product-box']", "div[class*='list_box']", "div[class*='prod']"],
            title_selectors=[".prod-name", "a[title]", "span[class*='title']", "div[class*='name']"],
            link_selectors=["a[title]", "a"]
        )
        for item in firstcry_items:
            if item["link"]:
                if item["link"].startswith("//"):
                    item["link"] = f"https:{item['link']}"
                elif not item["link"].startswith("http"):
                    item["link"] = f"https://www.firstcry.com{item['link']}"
        all_in_stock.extend(firstcry_items)

        await browser.close()
        
    return all_in_stock
