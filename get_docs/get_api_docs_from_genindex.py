import requests
from bs4 import BeautifulSoup
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from queue import Queue

GENINDEX_PATH = "kernel_docs_html/genindex.html"
BASE_URL = "https://www.kernel.org/doc/html/latest/"
SAVE_DIR = "api_docs_txt_genindex_with_return"

class ThreadSafeCounter:
    def __init__(self):
        self._counter = 0
        self._lock = threading.Lock()
    
    def increment(self):
        with self._lock:
            self._counter += 1
            return self._counter
    
    @property
    def value(self):
        with self._lock:
            return self._counter

progress_counter = ThreadSafeCounter()
success_counter = ThreadSafeCounter()
failed_counter = ThreadSafeCounter()
skipped_counter = ThreadSafeCounter()

type_counters = {
    'function': ThreadSafeCounter(),
    'macro': ThreadSafeCounter(), 
    'enum': ThreadSafeCounter(),
    'struct': ThreadSafeCounter(),
    'type': ThreadSafeCounter(),
    'variable': ThreadSafeCounter(),
    'member': ThreadSafeCounter(),
    'other': ThreadSafeCounter()
}

def get_api_type(text):
    text_lower = text.lower()
    if "(c function)" in text_lower:
        return "function"
    elif "(c macro)" in text_lower:
        return "macro"
    elif "(c enum)" in text_lower or "(c enumerator)" in text_lower:
        return "enum"
    elif "(c struct)" in text_lower or "(c union)" in text_lower:
        return "struct"
    elif "(c type)" in text_lower:
        return "type"
    elif "(c variable)" in text_lower or "(c var)" in text_lower:
        return "variable"
    elif "(c member)" in text_lower:
        return "member"
    else:
        return "other"

def clean_api_name(text, api_type):
    suffixes = [
        " (C function)", " (c function)",
        " (C macro)", " (c macro)", 
        " (C enum)", " (c enum)",
        " (C enumerator)", " (c enumerator)",
        " (C struct)", " (c struct)",
        " (C union)", " (c union)",
        " (C type)", " (c type)",
        " (C variable)", " (c variable)",
        " (C var)", " (c var)",
        " (C member)", " (c member)"
    ]
    
    for suffix in suffixes:
        if text.endswith(suffix):
            return text[:-len(suffix)].strip()
    
    return text

def analyze_api_types_only():
    print(f"📊 Analyzing API types in {GENINDEX_PATH}...")
    
    with open(GENINDEX_PATH, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    
    type_stats = {}
    type_examples = {}
    total_apis = 0
    
    for a in soup.select("table.genindextable li a"):
        href = a.get("href")
        text = a.text.strip()
        
        c_indicators = ["(c function)", "(c macro)", "(c enum)", "(c enumerator)", 
                       "(c struct)", "(c union)", "(c type)", "(c variable)", 
                       "(c var)", "(c member)"]
        
        if href and text and any(indicator in text.lower() for indicator in c_indicators):
            if href.endswith(".html") or ".html#" in href:
                api_type = get_api_type(text)
                api_name = clean_api_name(text, api_type)
                
                type_stats[api_type] = type_stats.get(api_type, 0) + 1
                
                if api_type not in type_examples:
                    type_examples[api_type] = []
                if len(type_examples[api_type]) < 3:
                    type_examples[api_type].append(api_name)
                
                total_apis += 1
    
    print(f"\n{'='*60}")
    print(f"📊 Linux Kernel API Type Analysis")
    print(f"{'='*60}")
    print(f"📋 Total API entries: {total_apis}")
    print(f"🔍 API types found: {len(type_stats)}")
    
    print(f"\n📈 Detailed Statistics:")
    print(f"{'Type':<12} {'Count':<8} {'Percent':<8} {'Examples'}")
    print(f"{'-'*60}")
    
    sorted_types = sorted(type_stats.items(), key=lambda x: x[1], reverse=True)
    
    for api_type, count in sorted_types:
        percentage = (count / total_apis * 100) if total_apis > 0 else 0
        examples = ', '.join(type_examples.get(api_type, [])[:3])
        print(f"{api_type.capitalize():<12} {count:<8} {percentage:>6.1f}%   {examples}")
    
    report_file = "api_types_analysis.txt"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(f"Linux Kernel API Type Analysis Report\n")
        f.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"{'='*60}\n\n")
        f.write(f"Total: {total_apis} API entries\n")
        f.write(f"Types found: {len(type_stats)}\n\n")
        
        f.write("Detailed Statistics:\n")
        for api_type, count in sorted_types:
            percentage = (count / total_apis * 100) if total_apis > 0 else 0
            f.write(f"{api_type.capitalize()}: {count} ({percentage:.1f}%)\n")
            
            examples = type_examples.get(api_type, [])
            if examples:
                f.write(f"  Examples: {', '.join(examples)}\n")
            f.write("\n")
    
    print(f"\n📝 Detailed report saved to: {report_file}")
    
    return type_stats, type_examples

def get_api_links():
    with open(GENINDEX_PATH, "r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")
    links = []
    
    for a in soup.select("table.genindextable li a"):
        href = a.get("href")
        text = a.text.strip()
        
        c_indicators = ["(c function)", "(c macro)", "(c enum)", "(c enumerator)", 
                       "(c struct)", "(c union)", "(c type)", "(c variable)", 
                       "(c var)", "(c member)"]
        
        if href and text and any(indicator in text.lower() for indicator in c_indicators):
            if href.endswith(".html") or ".html#" in href:
                api_type = get_api_type(text)
                api_name = clean_api_name(text, api_type)
                links.append((api_name, href, api_type))
    
    return links

def extract_api_text(html, api_name, api_type):
    soup = BeautifulSoup(html, "html.parser")
    
    api_id = f"c.{api_name}"
    api_element = soup.find(id=api_id)
    
    if not api_element:
        return f"{api_type.capitalize()} {api_name} not found in this page."
    
    texts = []
    
    if api_element.name == 'dt':
        sig_text = api_element.get_text(" ", strip=True)
        sig_text = re.sub(r'\s*¶.*$', '', sig_text)
        texts.append(f"{api_type.capitalize()}: {sig_text}")
    
    dd = api_element.find_next_sibling('dd')
    if dd:
        desc = dd.get_text(" ", strip=True)
        texts.append(desc)
    
    current = api_element.parent
    while current:
        next_sibling = current.find_next_sibling()
        if not next_sibling:
            break
        if (next_sibling.name == 'div' and 
            'kernelindent' in next_sibling.get('class', [])):
            
            formatted_parts = []
            
            for section_name in ['Parameters', 'Members', 'Fields', 'Values']:
                section_strong = next_sibling.find('strong', string=section_name)
                if section_strong:
                    formatted_parts.append(section_name)
                    section_dl = section_strong.find_next('dl')
                    if section_dl:
                        for dt in section_dl.find_all('dt'):
                            item_name = dt.get_text(" ", strip=True)
                            dd_item = dt.find_next_sibling('dd')
                            if dd_item:
                                item_desc = dd_item.get_text(" ", strip=True)
                                formatted_parts.append(f"  {item_name}")
                                formatted_parts.append(f"    {item_desc}")
            
            desc_strong = next_sibling.find('strong', string='Description')
            if desc_strong:
                formatted_parts.append("\nDescription")
                desc_p = desc_strong.find_next('p')
                if desc_p:
                    desc_text = desc_p.get_text(" ", strip=True)
                    formatted_parts.append(f"  {desc_text}")
            
            return_strong = next_sibling.find('strong', string='Return')
            if return_strong:
                formatted_parts.append("\nReturn")
                return_p = return_strong.find_next('p')
                if return_p:
                    return_text = return_p.get_text(" ", strip=True)
                    formatted_parts.append(f"  {return_text}")
                else:
                    return_dl = return_strong.find_next('dl')
                    if return_dl:
                        return_items = []
                        for dt in return_dl.find_all('dt'):
                            item_name = dt.get_text(" ", strip=True)
                            dd_item = dt.find_next_sibling('dd')
                            if dd_item:
                                item_desc = dd_item.get_text(" ", strip=True)
                                return_items.append(f"  {item_name}: {item_desc}")
                        if return_items:
                            formatted_parts.extend(return_items)
                    else:
                        next_element = return_strong.next_sibling
                        if next_element and isinstance(next_element, str) and next_element.strip():
                            formatted_parts.append(f"  {next_element.strip()}")
                        elif next_element and hasattr(next_element, 'get_text'):
                            return_text = next_element.get_text(" ", strip=True)
                            if return_text:
                                formatted_parts.append(f"  {return_text}")
            
            if formatted_parts:
                texts.append("\n".join(formatted_parts))
            break
        current = next_sibling
    
    return "\n\n".join(texts) if texts else f"No content found for {api_name}"

def download_single_api(api_info, existing_files, total_count):
    api_name, href, api_type = api_info
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", api_name)
    save_path = os.path.join(SAVE_DIR, f"{api_type}_{safe_name}.txt")
    
    current = progress_counter.increment()
    
    filename_key = f"{api_type}_{safe_name}"
    if filename_key in existing_files:
        skipped_counter.increment()
        type_counters[api_type].increment()
        print(f"[{current}/{total_count}] Skipped (exists): {filename_key}")
        return ("skipped", api_name, None)
    
    url = BASE_URL + href.lstrip("./")
    
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        api_text = extract_api_text(resp.text, api_name, api_type)
        
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(api_text)
        
        success_counter.increment()
        type_counters[api_type].increment()
        print(f"[{current}/{total_count}] Downloaded: {api_type}_{safe_name}")
        return ("success", api_name, None)
        
    except Exception as e:
        failed_counter.increment()
        error_msg = str(e)
        print(f"[{current}/{total_count}] Failed: {api_name} ({error_msg})")
        return ("failed", api_name, (url, error_msg))

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Linux Kernel API Documentation Retrieval Tool')
    parser.add_argument('--mode', choices=['analyze', 'download'], default='download',
                       help='Mode: analyze=statistics only, download=download docs')
    parser.add_argument('--threads', type=int, default=20,
                       help='Number of download threads (default: 20)')
    
    args = parser.parse_args()
    
    if args.mode == 'analyze':
        print("🔍 Mode: Analysis only")
        analyze_api_types_only()
        return
    
    print("📥 Mode: Download documentation")
    
    os.makedirs(SAVE_DIR, exist_ok=True)
    api_links = get_api_links()
    total_count = len(api_links)
    print(f"Found {total_count} API entries in genindex.html.")
    
    existing_files = set()
    if os.path.exists(SAVE_DIR):
        existing_files = {f.replace('.txt', '') for f in os.listdir(SAVE_DIR) 
                         if f.endswith('.txt')}
    
    global progress_counter, success_counter, failed_counter, skipped_counter
    progress_counter = ThreadSafeCounter()
    success_counter = ThreadSafeCounter()
    failed_counter = ThreadSafeCounter()
    skipped_counter = ThreadSafeCounter()
    
    failed_apis = []
    
    max_workers = args.threads
    print(f"Using {max_workers} threads for downloading...")
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(download_single_api, api_info, existing_files, total_count): api_info 
            for api_info in api_links
        }
        
        for future in as_completed(futures):
            try:
                result = future.result()
                status, api_name, error_info = result
                
                if status == "failed" and error_info:
                    url, error_msg = error_info
                    failed_apis.append((api_name, url, error_msg))
                    
            except Exception as e:
                api_info = futures[future]
                api_name = api_info[0]
                print(f"Unexpected error for {api_name}: {e}")
                failed_apis.append((api_name, "unknown_url", str(e)))
    
    elapsed_time = time.time() - start_time
    
    print(f"\n=== 📊 Download Summary ===")
    print(f"📋 Total APIs: {total_count}")
    print(f"⏭️  Skipped (existing): {skipped_counter.value}")
    print(f"✅ Successfully downloaded: {success_counter.value}")
    print(f"❌ Failed: {failed_counter.value}")
    print(f"⏱️  Time elapsed: {elapsed_time:.2f} seconds")
    print(f"🚀 Average speed: {total_count/elapsed_time:.2f} APIs/second")
    
    print(f"\n=== 📈 Downloaded by Type ===")
    total_downloaded = sum(counter.value for counter in type_counters.values())
    for api_type, counter in type_counters.items():
        count = counter.value
        if count > 0:
            percentage = (count / total_downloaded * 100) if total_downloaded > 0 else 0
            print(f"  📄 {api_type.capitalize()}: {count} ({percentage:.1f}%)")
    
    if failed_apis:
        with open("failed_apis.txt", "w", encoding="utf-8") as f:
            for api_name, url, error in failed_apis:
                f.write(f"{api_name}\t{url}\t{error}\n")
        print(f"Failed APIs saved to failed_apis.txt")
    
    print(f"\n📁 All files saved to: {SAVE_DIR}")
    print(f"📋 File naming convention: [type]_[name].txt")

if __name__ == "__main__":
    main()
