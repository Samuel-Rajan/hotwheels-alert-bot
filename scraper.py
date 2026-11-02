import logging
from playwright.async_api import async_playwright

async def fetch_all_stores():
    """Asynchronously scrapes Hot Wheels stock across platforms using direct search URLs."""
    all_in_stock = []
    
    async with async_playwright() as p:
        # Set headless=True (or headless=False if you want to inspect browser actions)
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        # --- 1. BLINKIT ---
        try:
            logging.info("Checking Blinkit via browser...")
            # Go directly to search URL
            await page.goto("https://blinkit.com/s/?q=hot%20wheels", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(4000)  # Allow JS hydration
            
            # Select product cards on search results
            items = await page.locator("a[href*='/prid/']").all()
            for item in items[:8]:
                try:
                    text_content = await item.inner_text()
                    title = text_content.split("\n")[0] if text_content else ""
                    link = await item.get_attribute("href")
                    
                    if "hot wheels" in text_content.lower():
                        all_in_stock.append({
                            "platform": "Blinkit",
                            "id": link if link else title,
                            "title": title if title else "Hot Wheels Product",
                            "price": "Check App",
                            "mrp": "",
                            "image": "",
                            "link": f"https://blinkit.com{link}" if link and link.startswith("/") else "https://blinkit.com"
                        })
                except Exception:
                    continue
        except Exception as e:
            logging.error(f"Blinkit scraper error: {e}")

        # --- 2. ZEPTO ---
        try:
            logging.info("Checking Zepto via browser...")
            await page.goto("https://www.zeptonow.com/search?query=hot%20wheels", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(4000)
            
            items = await page.locator("a[data-testid='product-card']").all()
            for item in items[:8]:
                try:
                    title = await item.locator("h4, [class*='title']").first.inner_text()
                    link = await item.get_attribute("href")
                    if "hot wheels" in title.lower():
                        all_in_stock.append({
                            "platform": "Zepto",
                            "id": link if link else title,
                            "title": title,
                            "price": "Check App",
                            "mrp": "",
                            "image": "",
                            "link": f"https://www.zeptonow.com{link}" if link and link.startswith("/") else "https://www.zeptonow.com"
                        })
                except Exception:
                    continue
        except Exception as e:
            logging.error(f"Zepto scraper error: {e}")

        # --- 3. FIRSTCRY ---
        try:
            logging.info("Checking FirstCry via browser...")
            await page.goto("https://www.firstcry.com/search?q=hot%20wheels", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(4000)
            
            items = await page.locator(".prod-item, div[class*='product-box']").all()
            for item in items[:8]:
                try:
                    title_elem = item.locator(".prod-name, a[title]").first
                    title = await title_elem.inner_text()
                    link_elem = item.locator("a").first
                    link = await link_elem.get_attribute("href") if link_elem else ""
                    
                    if "hot wheels" in title.lower():
                        all_in_stock.append({
                            "platform": "FirstCry",
                            "id": link if link else title,
                            "title": title,
                            "price": "Check App",
                            "mrp": "",
                            "image": "",
                            "link": "https:" + link if link.startswith("//") else f"https://www.firstcry.com{link}"
                        })
                except Exception:
                    continue
        except Exception as e:
            logging.error(f"FirstCry scraper error: {e}")

        await browser.close()
        
    return all_in_stock