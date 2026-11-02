import logging
import httpx
from bs4نهار import BeautifulSoup
import re
from bs4 import BeautifulSoup

async def fetch_all_stores():
    """
    Resilient scraper covering FirstCry, BigBasket, Blinkit, Instamart, and Zepto 
    with individual error-isolation and status code handling.
    """
    all_in_stock = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache"
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
                            
                            all_in_stock.append({
                                "platform": "FirstCry",
                                "id": prod_id,
                                "title": text[:80],
                                "price": "Check App",
                                "link": link
                            })
        except Exception as e:
            logging.error(f"[FirstCry] Error: {e}")

        # --- 2. BIGBASKET ---
        try:
            logging.info("[BigBasket] Querying catalog search...")
            bb_url = "https://www.bigbasket.com/search/?slug=hot%20wheels"
            res = await client.get(bb_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                products = soup.select("div[qa='productCard'], div[class*='ProductCard'], a[href*='/pd/']")
                for prod in products:
                    text = prod.get_text(" ", strip=True)
                    if "hot wheels" in text.lower():
                        a_tag = prod if prod.name == 'a' else prod.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"https://www.bigbasket.com{href}" if href.startswith('/') else href or bb_url
                        prod_id = href.split('/')[-2] if '/' in href else "bb_hotwheels"
                        
                        all_in_stock.append({
                            "platform": "BigBasket",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[BigBasket] Error: {e}")

        # --- 3. BLINKIT ---
        try:
            logging.info("[Blinkit] Querying search endpoint...")
            blinkit_url = "https://blinkit.com/s/?q=hot%20wheels"
            res = await client.get(blinkit_url, headers=headers)
            if res.status_code in [200, 202]:
                soup = BeautifulSoup(res.text, 'html.parser')
                items = soup.select("div[class*='Product'], a[href*='/prid/']")
                for item in items:
                    text = item.get_text(" ", strip=True)
                    if "hot wheels" in text.lower():
                        a_tag = item if item.name == 'a' else item.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"https://blinkit.com{href}" if href.startswith('/') else href or blinkit_url
                        prod_id = href.split('/')[-1] if href else "blinkit_hw"
                        
                        all_in_stock.append({
                            "platform": "Blinkit",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[Blinkit] Error: {e}")

        # --- 4. SWIGGY INSTAMART ---
        try:
            logging.info("[Instamart] Querying store inventory...")
            instamart_url = "https://www.swiggy.com/instamart/search?query=hot%20wheels"
            res = await client.get(instamart_url, headers=headers)
            if res.status_code in [200, 202]:
                soup = BeautifulSoup(res.text, 'html.parser')
                cards = soup.select("div[class*='ItemCard'], a[href*='/item/']")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    if "hot wheels" in text.lower():
                        a_tag = card if card.name == 'a' else card.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"https://www.swiggy.com{href}" if href.startswith('/') else href or instamart_url
                        prod_id = href.split('/')[-1] if href else "instamart_hw"
                        
                        all_in_stock.append({
                            "platform": "Instamart",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[Instamart] Error: {e}")

        # --- 5. ZEPTO ---
        try:
            logging.info("[Zepto] Querying catalog...")
            zepto_url = "https://www.zeptonow.com/search?query=hot%20wheels"
            res = await client.get(zepto_url, headers=headers)
            if res.status_code in [200, 202]:
                soup = BeautifulSoup(res.text, 'html.parser')
                cards = soup.select("a[href*='/pn/'], div[class*='product']")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    if "hot wheels" in text.lower():
                        a_tag = card if card.name == 'a' else card.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"https://www.zeptonow.com{href}" if href.startswith('/') else href or zepto_url
                        prod_id = href.split('/')[-1] if href else "zepto_hw"
                        
                        all_in_stock.append({
                            "platform": "Zepto",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[Zepto] Error: {e}")

    return all_in_stock
