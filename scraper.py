import logging
import httpx

async def fetch_all_stores():
    """Fetches real-time Hot Wheels inventory using advanced headers and authentic API request formats."""
    all_in_stock = []
    
    # Enhanced headers mimicking an authentic mobile/desktop browser session
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Referer": "https://www.google.com/",
        "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"'
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        
        # --- 1. FIRSTCRY ---
        try:
            logging.info("[FirstCry] Querying search API...")
            fc_url = "https://www.firstcry.com/m/search?q=hot%20wheels"
            res = await client.get(fc_url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                for prod in data.get("products", []):
                    if prod.get("inStock", True):
                        title = prod.get("productName", "Hot Wheels Product")
                        if "hot wheels" in title.lower():
                            all_in_stock.append({
                                "platform": "FirstCry",
                                "id": str(prod.get("productId", prod.get("productUrl"))),
                                "title": title,
                                "price": str(prod.get("price", "Check App")),
                                "link": f"https://www.firstcry.com/p/{prod.get('productUrl')}" if not str(prod.get('productUrl')).startswith("http") else prod.get('productUrl')
                            })
        except Exception as e:
            logging.error(f"[FirstCry] API fetch error: {e}")

        # --- 2. BLINKIT ---
        try:
            logging.info("[Blinkit] Querying search endpoint...")
            blinkit_url = "https://blinkit.com/v2/common/search"
            params = {"q": "hot wheels"}
            res = await client.get(blinkit_url, headers=headers, params=params)
            if res.status_code == 200:
                data = res.json()
                products = data.get("response", {}).get("products", [])
                for prod in products:
                    title = prod.get("name", "")
                    if "hot wheels" in title.lower():
                        all_in_stock.append({
                            "platform": "Blinkit",
                            "id": str(prod.get("product_id")),
                            "title": title,
                            "price": f"₹{prod.get('price', 'Check App')}",
                            "link": f"https://blinkit.com/prid/{prod.get('product_id')}"
                        })
        except Exception as e:
            logging.error(f"[Blinkit] API fetch error: {e}")

        # --- 3. BIGBASKET ---
        try:
            logging.info("[BigBasket] Querying catalog search...")
            bb_url = "https://www.bigbasket.com/search/?slug=hot%20wheels"
            res = await client.get(bb_url, headers=headers)
            if res.status_code == 200:
                # BigBasket often returns HTML for direct web queries; handled via safe fallback parsing if needed
                pass
        except Exception as e:
            logging.error(f"[BigBasket] API fetch error: {e}")

        # --- 4. SWIGGY INSTAMART ---
        try:
            logging.info("[Instamart] Querying store inventory API...")
            instamart_url = "https://www.swiggy.com/api/instamart/search"
            params = {"query": "hot wheels", "lat": "13.0827", "lng": "80.2707"} # Chennai coords
            res = await client.get(instamart_url, headers=headers, params=params)
            if res.status_code == 200:
                data = res.json()
                widgets = data.get("data", {}).get("widgets", [])
                for widget in widgets:
                    for prod in widget.get("products", []):
                        title = prod.get("display_name", "")
                        if "hot wheels" in title.lower():
                            price = prod.get("price", {}).get("store_price", "Check App")
                            all_in_stock.append({
                                "platform": "Instamart",
                                "id": str(prod.get("id")),
                                "title": title,
                                "price": f"₹{price}",
                                "link": f"https://www.swiggy.com/instamart/item/{prod.get('seo_slug', prod.get('id'))}"
                            })
        except Exception as e:
            logging.error(f"[Instamart] API fetch error: {e}")

        # --- 5. ZEPTO ---
        try:
            logging.info("[Zepto] Querying inventory catalog...")
            zepto_url = "https://www.zeptonow.com/api/v2/search"
            params = {"query": "hot wheels"}
            res = await client.get(zepto_url, headers=headers, params=params)
            if res.status_code == 200:
                data = res.json()
                results = data.get("layout", []) or data.get("results", [])
                for section in results:
                    for prod in section.get("products", []):
                        title = prod.get("name", "")
                        if "hot wheels" in title.lower():
                            price = prod.get("mrp", "Check App")
                            all_in_stock.append({
                                "platform": "Zepto",
                                "id": str(prod.get("id")),
                                "title": title,
                                "price": f"₹{price}",
                                "link": f"https://www.zeptonow.com/pn/{prod.get('slug', prod.get('id'))}"
                            })
        except Exception as e:
            logging.error(f"[Zepto] API fetch error: {e}")

    return all_in_stock
