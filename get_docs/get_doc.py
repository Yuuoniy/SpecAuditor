import requests
from bs4 import BeautifulSoup
import os
import time

BASE_URL = "https://www.kernel.org/doc/html/latest/"
INDEX_URL = BASE_URL + "genindex.html"
SAVE_DIR = "kernel_docs_html"
os.makedirs(SAVE_DIR, exist_ok=True)

def get_index_links():
    resp = requests.get(INDEX_URL)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    for a in soup.select("a"):
        href = a.get("href")
        if href and not href.startswith("http") and href.endswith(".html"):
            links.append(href)
    return sorted(set(links))

def download_doc_page(href):
    url = BASE_URL + href
    try:
        resp = requests.get(url)
        resp.raise_for_status()
        filename = os.path.join(SAVE_DIR, href.replace("/", "_"))
        with open(filename, "w", encoding="utf-8") as f:
            f.write(resp.text)
        print(f"Saved: {filename}")
    except Exception as e:
        print(f"Failed: {url} ({e})")

if __name__ == "__main__":
    print("Fetching index links...")
    links = get_index_links()
    print(f"Found {len(links)} doc pages.")
    for href in links:
        download_doc_page(href)
        time.sleep(0.5)

    print("All docs downloaded.")