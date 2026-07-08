#!/usr/bin/env python3
"""Test the CHP XML feed directly."""

import requests
import re
from bs4 import BeautifulSoup

CHP_XML_FEED_URL = "http://media.chp.ca.gov/sa_xml/sa.xml"
CHP_DISPATCH_BARSTOW = "BSCC"

print(f"Fetching CHP XML feed from {CHP_XML_FEED_URL}...")
try:
    response = requests.get(CHP_XML_FEED_URL, timeout=30)
    print(f"Response status: {response.status_code}")
    print(f"Response length: {len(response.text)}")
    response.encoding = 'utf-8'
    
    # Check if Barstow dispatch is in the feed
    if CHP_DISPATCH_BARSTOW in response.text:
        print(f"[OK] Barstow dispatch ({CHP_DISPATCH_BARSTOW}) found in feed")
        
        # Count Log blocks for Barstow
        import re
        dispatch_pattern = f'<Dispatch ID = "{CHP_DISPATCH_BARSTOW}">(.*?)</Dispatch>'
        dispatch_match = re.search(dispatch_pattern, response.text, re.S)
        if dispatch_match:
            dispatch_content = dispatch_match.group(1)
            log_pattern = r'<Log ID = "([^"]+)">'
            logs = re.findall(log_pattern, dispatch_content)
            print(f"[OK] Found {len(logs)} log entries for Barstow")
            if logs:
                print(f"Sample log IDs: {logs[:5]}")
        else:
            print("[ERROR] Could not find dispatch content")
    else:
        print(f"[ERROR] Barstow dispatch ({CHP_DISPATCH_BARSTOW}) NOT found in feed")
    
    # Show what dispatches are available (always show this)
    dispatch_pattern = r'<Dispatch ID = "([^"]+)">'
    all_dispatches = re.findall(dispatch_pattern, response.text)
    print(f"Available dispatches: {all_dispatches[:10]}")
        
except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
