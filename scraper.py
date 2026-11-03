import logging
import httpx
from bs4 import BeautifulSoup
import re
from playwright.async_api import async_playwright

async def fetch_all_stores():
    all_in_stock = []
    
    # Advanced mobile headers to bypass WAF / Cloudflare
    stealth_headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "x-location-context": "13.0827,80.2707",
    }

    # ==========================================
    # 1. FIRSTCRY (Playwright Headless Browser)
    # ==========================================
    try:
        logging.info("[FirstCry] Launching headless browser for JS rendering...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
                viewport={"width": 390, "height": 844},
                locale="en-IN"
            )
            page = await context.new_page()
            await page.goto("https://www.firstcry.com/search?q=hot%20wheels&sort=newness", timeout=15000)
            
            try:
                await page.wait_for_selector("div[class*='product'], li[class*='product'], .prod-item, a[href*='/p/']", timeout=8000)
            except Exception:
                pass

            cards = await page.locator("div[class*='product'], li[class*='product'], .prod-item, a[href*='/p/']").all()
            for card in cards:
                try:
                    text = await card.inner_text()
                    text_lower = text.lower()
                    
                    if "hot wheels" in text_lower and "out of stock" not in text_lower and "sold out" not in text_lower:
                        link_elem = card if await card.evaluate("node => node.tagName === 'A'") else card.locator("a[href]").first
                        href = await link_elem.get_attribute("href") if await link_elem.count() > 0 else ""
                        
                        if not href or href == "#" or href.startswith("javascript:"):
                            continue

                        link = href if href.startswith("http") else f"https://www.firstcry.com{href}"
                        prod_id = link.split('?')[0].split('/')[-1]

                        if not prod_id or len(prod_id) < 2:
                            continue

                        price = "Check App"
                        for word in text.split():
                            if "₹" in word or "INR" in word:
                                price = word
                                break

                        all_in_stock.append({
                            "platform": "FirstCry",
                            "id": prod_id,
                            "title": text.replace("\n", " ")[:80],
                            "price": price,
                            "link": link
                        })
                except Exception:
                    continue
            await browser.close()
    except Exception as e:
        logging.error(f"[FirstCry Playwright] Error: {e}")

    # ==========================================
    # QUICK-COMMERCE APPS (HTTPX with Stealth Headers)
    # ==========================================
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True, http2=True) as client:
        
        # --- 2. BIGBASKET ---
        try:
            logging.info("[BigBasket] Querying catalog search...")
            bb_url = "https://www.bigbasket.com/search/?slug=hot%20wheels"
            res = await client.get(bb_url, headers=stealth_headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                products = soup.select("div[qa='productCard'], div[class*='ProductCard'], a[href*='/pd/']")
                for prod in products:
                    text = prod.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
                        a_tag = prod if prod.name == 'a' else prod.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"
