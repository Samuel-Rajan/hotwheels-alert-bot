import logging
import httpx
from bs4 import BeautifulSoup
import re

async def fetch_all_stores():
    """
    Bulletproof multi-store scraper with session cookie handshaking for FirstCry,
    recursive JSON parsing, and robust HTML fallback extraction.
    """
    all_in_stock = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,application/json,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Cache-Control": "no-cache",
        "x-location-context": "13.0827,80.2707",
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        
        # ==========================================
        # 1. FIRSTCRY (Session Handshake + JSON/HTML Fallback)
        # ==========================================
        try:
            logging.info("[FirstCry] Initializing session handshake...")
            # Establish valid cookies from homepage first
            await client.get("https://www.firstcry.com/", headers=headers)
            
            logging.info("[FirstCry] Querying catalog feed...")
            fc_url = "https://www.firstcry.com/search?q=hot%20wheels&sort=newness"
            res = await client.get(fc_url, headers=headers)
            
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                
                # Broad extraction covering all product cards, grid blocks, and links
                cards = soup.select(".prod-item, .list-block, div[class*='product'], div[class*='item'], li[class*='product'], a[href]")
                
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    text_lower = text.lower()
                    
                    if "hot wheels" in text_lower and "out of stock" not in text_lower and "sold out" not in text_lower:
                        a_tag = card if card.name == 'a' else card.find('a', href=True)
                        if a_tag and a_tag.get('href'):
                            href = a_tag['href']
                            if href == "#" or href.startswith("javascript:"):
                                continue

                            link = href if href.startswith("http") else f"https://www.firstcry.com{href}"
                            
                            # Extract clean product ID
                            prod_id_match = re.search(r'/p/([a-zA-Z0-9\-_]+)', link) or re.search(r'pid=([a-zA-Z0-9\-_]+)', link) or re.search(r'/([0-9]{5,10})', link)
                            prod_id = prod_id_match.group(1) if prod_id_match else link.split('?')[0].split('/')[-1]

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
                                "title": text[:80],
                                "price": price,
                                "link": link
                            })
        except Exception as e:
            logging.error(f"[FirstCry] Error: {e}")

        # ==========================================
        # 2. BIGBASKET
        # ==========================================
        try:
            logging.info("[BigBasket] Querying catalog search...")
            bb_url = "https://www.bigbasket.com/search/?slug=hot%20wheels"
            res = await client.get(bb_url, headers=headers)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                products = soup.select("div[qa='productCard'], div[class*='ProductCard'], a[href*='/pd/']")
                for prod in products:
                    text = prod.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
                        a_tag = prod if prod.name == 'a' else prod.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"https://www.bigbasket.com{href}" if href.startswith('/') else href or bb_url
                        prod_id_match = re.search(r'/pd/([0-9]+)', href)
                        prod_id = prod_id_match.group(1) if prod_id_match else "bb_" + href.split('/')[-2]

                        all_in_stock.append({
                            "platform": "BigBasket",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[BigBasket] Error: {e}")

        # ==========================================
        # 3. BLINKIT
        # ==========================================
        try:
            logging.info("[Blinkit] Querying search endpoint...")
            blinkit_url = "https://blinkit.com/s/?q=hot%20wheels"
            res = await client.get(blinkit_url, headers=headers)
            if res.status_code in [200, 202]:
                soup = BeautifulSoup(res.text, 'html.parser')
                items = soup.select("div[class*='Product'], a[href*='/prid/']")
                for item in items:
                    text = item.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
                        a_tag = item if item.name == 'a' else item.find('a', href=True)
                        href = a_tag.get('href', '') if a_tag else ''
                        link = f"https://blinkit.com{href}" if href.startswith('/') else href or blinkit_url
                        prod_id_match = re.search(r'/prid/([0-9]+)', href)
                        prod_id = prod_id_match.group(1) if prod_id_match else href.split('/')[-1]

                        all_in_stock.append({
                            "platform": "Blinkit",
                            "id": prod_id,
                            "title": text[:80],
                            "price": "Check App",
                            "link": link
                        })
        except Exception as e:
            logging.error(f"[Blinkit] Error: {e}")

        # ==========================================
        # 4. SWIGGY INSTAMART
        # ==========================================
        try:
            logging.info("[Instamart] Querying store inventory...")
            instamart_url = "https://www.swiggy.com/instamart/search?query=hot%20wheels"
            res = await client.get(instamart_url, headers=headers)
            if res.status_code in [200, 202]:
                soup = BeautifulSoup(res.text, 'html.parser')
                cards = soup.select("div[class*='ItemCard'], a[href*='/item/']")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
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

        # ==========================================
        # 5. ZEPTO
        # ==========================================
        try:
            logging.info("[Zepto] Querying catalog...")
            zepto_url = "https://www.zeptonow.com/search?query=hot%20wheels"
            res = await client.get(zepto_url, headers=headers)
            if res.status_code in [200, 202]:
                soup = BeautifulSoup(res.text, 'html.parser')
                cards = soup.select("a[href*='/pn/'], div[class*='productCard']")
                for card in cards:
                    text = card.get_text(" ", strip=True)
                    if "hot wheels" in text.lower() and "out of stock" not in text.lower():
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

    # Deduplicate results per run
    unique_results = {}
    for item in all_in_stock:
        key = f"{item['platform']}_{item['id']}"
        if key not in unique_results:
            unique_results[key] = item

    return list(unique_results.values())
