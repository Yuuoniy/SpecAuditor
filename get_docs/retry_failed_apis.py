#!/usr/bin/env python3
import os
import re
import time
import requests
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from get_api_docs_from_genindex import extract_api_text, SAVE_DIR, BASE_URL, get_api_type, clean_api_name


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

def detect_api_type_from_url(url, api_name):
    api_name_lower = api_name.lower()
    
    if any(pattern in api_name_lower for pattern in ['alloc', 'free', 'init', 'destroy', 'get', 'set', 'create', 'delete']):
        return "function"
    
    if any(pattern in api_name_lower for pattern in ['_max', '_min', '_mask', '_shift', '_flag']) or api_name.isupper():
        return "macro"
    
    if any(pattern in api_name_lower for pattern in ['_struct', '_info', '_data', '_ops', '_operations']):
        return "struct"
    
    if api_name_lower.endswith('_t'):
        return "type"
    
    return "function"

def retry_single_api(api_info, total_count, counter):
    if len(api_info) >= 3:
        api_name, url, api_type = api_info[:3]
    else:
        api_name, url = api_info[:2]
        api_type = detect_api_type_from_url(url, api_name)
    
    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", api_name)
    save_path = os.path.join(SAVE_DIR, f"{api_type}_{safe_name}.txt")
    
    current = counter.increment()
    
    old_save_path = os.path.join(SAVE_DIR, f"{safe_name}.txt")
    if os.path.exists(save_path) or os.path.exists(old_save_path):
        return ("exists", api_name, None)
    
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        api_text = extract_api_text(resp.text, api_name, api_type)
        
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(api_text)
        
        print(f"[{current}/{total_count}] Retry success: {api_type}_{safe_name}")
        return ("success", api_name, None)
        
    except Exception as e:
        error_msg = str(e)
        print(f"[{current}/{total_count}] Still failed: {api_name} ({error_msg})")
        return ("failed", api_name, (url, error_msg))

def retry_failed_apis():
    if not os.path.exists("failed_apis.txt"):
        print("No failed_apis.txt found. Nothing to retry.")
        return
    
    failed_apis = []
    with open("failed_apis.txt", "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 2:
                api_name, url = parts[0], parts[1]
                failed_apis.append((api_name, url))
    
    if not failed_apis:
        print("No failed APIs to retry.")
        return
    
    total_count = len(failed_apis)
    print(f"Retrying {total_count} failed APIs using 5 threads...")
    
    counter = ThreadSafeCounter()
    success_count = 0
    exists_count = 0
    still_failed = []
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(retry_single_api, api_info, total_count, counter): api_info 
            for api_info in failed_apis
        }
        
        for future in as_completed(futures):
            try:
                result = future.result()
                status, api_name, error_info = result
                
                if status == "success":
                    success_count += 1
                elif status == "exists":
                    exists_count += 1
                    print(f"[{counter.value}/{total_count}] Already exists: {api_name}")
                elif status == "failed" and error_info:
                    url, error_msg = error_info
                    still_failed.append((api_name, url, error_msg))
                    
            except Exception as e:
                api_info = futures[future]
                api_name = api_info[0]
                print(f"Unexpected error for {api_name}: {e}")
                still_failed.append((api_name, "unknown_url", str(e)))
    
    elapsed_time = time.time() - start_time
    
    print(f"\n=== Retry Summary ===")
    print(f"Total retried: {total_count}")
    print(f"Already existed: {exists_count}")
    print(f"Success: {success_count}")
    print(f"Still failed: {len(still_failed)}")
    print(f"Time elapsed: {elapsed_time:.2f} seconds")
    print(f"Average speed: {total_count/elapsed_time:.2f} APIs/second")
    
    if still_failed:
        with open("failed_apis.txt", "w", encoding="utf-8") as f:
            for api_name, url, error in still_failed:
                f.write(f"{api_name}\t{url}\t{error}\n")
        print("Updated failed_apis.txt with remaining failures.")
    else:
        os.remove("failed_apis.txt")
        print("All retries successful! Removed failed_apis.txt")

if __name__ == "__main__":
    retry_failed_apis()