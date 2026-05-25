import requests
from bs4 import BeautifulSoup

url = "https://www.bing.com/search?q=amazon+17pro"
headers = {
    "User-Agent": "Mozilla/5.0"
}

resp = requests.get(url, headers=headers)
html = resp.text

soup = BeautifulSoup(html, "lxml")

results = []

# Bing 搜索结果核心结构
for item in soup.select("li.b_algo"):
    title_tag = item.select_one("h2 a")
    if not title_tag:
        continue

    title = title_tag.get_text(strip=True)
    link = title_tag.get("href")

    results.append({
        "title": title,
        "link": link
    })

for r in results:
    print(r)