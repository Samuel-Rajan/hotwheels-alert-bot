import logging
import httpx
from bs4 import BeautifulSoup
import re

async def fetch_all_stores():
    """
    Fetches real-time Hot Wheels inventory across all 5 platforms:
    FirstCry, BigBasket, Blinkit, Swiggy Instamart, and Zepto.
    """
    all_in_stock = []
    
    # Advanced mobile-browser emulating headers to minimize WAF/Cloudflare restrictions
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        
        # --- 1. FIRSTCRY ---
        try:
            logging.info("[FirstCry] Querying search page...")
            fc_url = "https://www.firstcry.com/search?q=hot%20wheels"
            res = await client.get(fc_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                cards = soup.select("div[class*='product'], div.l-box, li[class*='product'], a[href*='/p/']")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
                        a_tag = card if card.name == 'a' else card.find('a', href=True)
                        if a_tag and a_tag.get('href'):
                            href = a_tag['href']
                            link = href if href.startswith("http") else f"https://www.firstcry.com{href}"
                            prod_id_match = re.search(r'/p/([a-zA-Z0-9\-_]+)', link)
                            prod_id = prod_id_match.group(1) if prod_id_match else link
                            
                            price = "Check App"
                            for word in text.split():
                                if any(char.isdigit() for char in word) and len(word) < 8:
                                    if "₹" in word or "INR" in word or any(d.isdigit() for d in word):
                                        price = word
                                        break

                            all_in_stock.append({
                                "platform": "FirstCry",
                                "id": prod_id,
                                "title": text[:80],
                                "price": price,
                                "link": link
                            })
        except Exception as e:
            logging.error(f"[FirstCry] Fetch error: {e}")

        # --- 2. BIGBASKET ---
        try:
            logging.info("[BigBasket] Querying catalog search...")
            bb_url = "https://www.bigbasket.com/search/?slug=hot%20wheels"
            res = await client.get(bb_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                products = soup.select("div[qa='productCard'], div[class*='ProductCard']")
                for prod in products:
                    text = prod.get_text(" ", strip=True)
                    if "hot wheels" in text.lower():
                        a_tag = prod.find('a', href=True)
                        link = f"https://www.bigbasket.com{a_tag['href']}" if a_tag and a_tag.get('href') else bb_url
                        prod_id = a_tag['href'].split('/')[-2] if a_tag and a_tag.get('href') else "bb_hotwheels"
                        
                        all_in_stock.append({
                            "platform": "BigBasket",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[BigBasket] Fetch error: {e}")

        # --- 3. BLINKIT ---
        try:
            logging.info("[Blinkit] Querying search endpoint...")
            blinkit_url = "https://blinkit.com/s/?q=hot%20wheels"
            res = await client.get(blinkit_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                items = soup.select("div[class*='Product__'], div[class*='item'], a[href*='/prn/']")
                for item in items:
                    text = item.get_text(" ", strip=True)
                    if "hot wheels" in text.lower():
                        a_tag = item if item.name == 'a' else item.find('a', href=True)
                        link = f"https://blinkit.com{a_tag['href']}" if a_tag and a_tag.get('href') else "https://blinkit.com"
                        prod_id = link.split('/')[-1] if link else "blinkit_hw"
                        
                        all_in_stock.append({
                            "platform": "Blinkit",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[Blinkit] Fetch error: {e}")

        # --- 4. SWIGGY INSTAMART ---
        try:
            logging.info("[Instamart] Querying store inventory...")
            instamart_url = "https://www.swiggy.com/instamart/search?query=hot%20wheels"
            res = await client.get(instamart_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                cards = soup.select("div[class*='ItemCard'], div[class*='styles__StyledCard']")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    if "hot wheels" in text.lower():
                        a_tag = card.find('a', href=True)
                        link = f"https://www.swiggy.com{a_tag['href']}" if a_tag and a_tag.get('href') else "https://www.swiggy.com/instamart"
                        prod_id = link.split('/')[-1] if link else "instamart_hw"
                        
                        all_in_stock.append({
                            "platform": "Instamart",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[Instamart] Fetch error: {e}")

        # --- 5. ZEPTO ---
        try:
            logging.info("[Zepto] Querying catalog...")
            zepto_url = "https://www.zeptonow.com/search?query=hot%20wheels"
            res = await client.get(zepto_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                cards = soup.select("a[href*='/pn/'], div[class*='product']")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    if "hot wheels" in text.lower():
                        a_tag = card if card.name == 'a' else card.find('a', href=True)
                        link = f"https://www.zeptonow.com{a_tag['href']}" if a_tag and a_tag.get('href') else "https://www.zeptonow.com"
                        prod_id = link.split('/')[-1] if link else "zepto_hw"
                        
                        all_in_stock.append({
                            "platform": "Zepto",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[Zepto] Fetch error: {e}")

    return all_in_stock
