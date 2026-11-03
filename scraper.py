import logging
import httpx
from bs4 import BeautifulSoup
import re
import json

async def fetch_all_stores():
    """
    Robust multi-store scraper utilizing direct JSON APIs for FirstCry 
    and resilient HTML parsing for quick-commerce apps.
    """
    all_in_stock = []
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.firstcry.com/",
        "Cache-Control": "no-cache",
        "x-location-context": "13.0827,80.2707",
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        
        # ==========================================
        # 1. FIRSTCRY (Direct JSON API Endpoint)
        # ==========================================
        try:
            logging.info("[FirstCry] Querying JSON search API...")
            # FirstCry mobile search suggestion/catalog API endpoint
            fc_api_url = "https://www.firstcry.com/rest/search?q=hot%20wheels"
            res = await client.get(fc_api_url, headers=headers)
            
            if res.status_code == 200:
                try:
                    data = res.json()
                    # Parse products from JSON response array
                    products = data.get("result", data.get("products", data.get("items", [])))
                    for prod in products:
                        title = prod.get("name", prod.get("prodName", ""))
                        if "hot wheels" in title.lower():
                            prod_id = str(prod.get("id", prod.get("prodId", "")))
                            url_slug = prod.get("url", prod.get("prodUrl", ""))
                            link = url_slug if url_slug.startswith("http") else f"https://www.firstcry.com{url_slug}"
                            price = str(prod.get("price", prod.get("finalPrice", "Check App")))
                            
                            all_in_stock.append({
                                "platform": "FirstCry",
                                "id": prod_id or link,
                                "title": title[:80],
                                "price": f"₹{price}" if not price.startswith("₹") else price,
                                "link": link
                            })
                except Exception:
                    # Fallback to HTML scraping if response is HTML fallback
                    soup = BeautifulSoup(res.text, 'html.parser')
                    cards = soup.select("div[class*='product'], li[class*='product'], a[href*='/p/']")
                    for card in cards:
                        text = card.get_text(" ", strip=True)
                        if "hot wheels" in text.lower() and "out of stock" not in text.lower():
                            a_tag = card if card.name == 'a' else card.find('a', href=True)
                            if a_tag and a_tag.get('href'):
                                href = a_tag['href']
                                link = href if href.startswith("http") else f"https://www.firstcry.com{href}"
                                all_in_stock.append({
                                    "platform": "FirstCry",
                                    "id": link.split('/')[-1],
                                    "title": text[:80],
                                    "price": "Check App",
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

    unique_results = {}
    for item in all_in_stock:
        key = f"{item['platform']}_{item['id']}"
        if key not in unique_results:
            unique_results[key] = item

    return list(unique_results.values())
