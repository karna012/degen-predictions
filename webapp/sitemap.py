"""
Generate XML sitemap for SEO.
"""
from datetime import datetime

def generate_sitemap():
    """Generate sitemap.xml for search engines."""

    urls = [
        {
            "loc": "https://degenpredictions.com/",
            "changefreq": "daily",
            "priority": "1.0",
            "lastmod": datetime.utcnow().strftime("%Y-%m-%d"),
        },
        # Sports
        {
            "loc": "https://degenpredictions.com/sports",
            "changefreq": "daily",
            "priority": "0.9",
            "lastmod": datetime.utcnow().strftime("%Y-%m-%d"),
        },
        {
            "loc": "https://degenpredictions.com/sports/soccer",
            "changefreq": "daily",
            "priority": "0.9",
        },
        {
            "loc": "https://degenpredictions.com/sports/baseball",
            "changefreq": "daily",
            "priority": "0.9",
        },
        # Crypto
        {
            "loc": "https://degenpredictions.com/crypto",
            "changefreq": "daily",
            "priority": "0.9",
            "lastmod": datetime.utcnow().strftime("%Y-%m-%d"),
        },
        # Stocks
        {
            "loc": "https://degenpredictions.com/stocks",
            "changefreq": "daily",
            "priority": "0.9",
            "lastmod": datetime.utcnow().strftime("%Y-%m-%d"),
        },
        {
            "loc": "https://degenpredictions.com/stocks/us",
            "changefreq": "daily",
            "priority": "0.8",
        },
        {
            "loc": "https://degenpredictions.com/stocks/india",
            "changefreq": "daily",
            "priority": "0.8",
        },
        # Dubai Real Estate (NEW)
        {
            "loc": "https://degenpredictions.com/dubai",
            "changefreq": "daily",
            "priority": "0.95",
            "lastmod": datetime.utcnow().strftime("%Y-%m-%d"),
        },
        {
            "loc": "https://degenpredictions.com/dubai/guide",
            "changefreq": "weekly",
            "priority": "0.90",
            "lastmod": datetime.utcnow().strftime("%Y-%m-%d"),
        },
        # Info pages
        {
            "loc": "https://degenpredictions.com/terms",
            "changefreq": "monthly",
            "priority": "0.5",
        },
        {
            "loc": "https://degenpredictions.com/privacy",
            "changefreq": "monthly",
            "priority": "0.5",
        },
    ]

    xml = '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
'''

    for url_data in urls:
        xml += f'''  <url>
    <loc>{url_data["loc"]}</loc>
'''
        if "lastmod" in url_data:
            xml += f'''    <lastmod>{url_data["lastmod"]}</lastmod>
'''
        xml += f'''    <changefreq>{url_data["changefreq"]}</changefreq>
    <priority>{url_data["priority"]}</priority>
  </url>
'''

    xml += '''</urlset>
'''

    return xml

if __name__ == "__main__":
    print(generate_sitemap())
