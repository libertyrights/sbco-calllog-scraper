#!/usr/bin/env python3
"""Test different CHP dispatch codes to find the correct one for Barstow area."""

import requests
import re

CHP_XML_FEED_URL = "http://media.chp.ca.gov/sa_xml/sa.xml"

# Potential dispatch codes for Barstow/Inland area
possible_codes = [
    "BSCC",  # Original code in scraper
    "SACC",  # Found in feed (might be San Antonio?)
    "STCC",  # Found in feed
    "TKCC",  # Found in feed
    "CHCC",  # Found in feed
    "INCC",  # Inland Communications Center (guess)
    "ICC",   # Inland Communications Center (guess)
]

print(f"Fetching CHP XML feed from {CHP_XML_FEED_URL}...")
try:
    response = requests.get(CHP_XML_FEED_URL, timeout=30)
    print(f"Response status: {response.status_code}")
    print(f"Response length: {len(response.text)}")
    
    # Get all available dispatch codes
    dispatch_pattern = r'<Dispatch ID = "([^"]+)">'
    all_dispatches = re.findall(dispatch_pattern, response.text)
    print(f"\nAll available dispatch codes in feed: {all_dispatches}")
    
    # Check each potential code
    print("\nTesting potential Barstow dispatch codes:")
    for code in possible_codes:
        if code in all_dispatches:
            print(f"[OK] {code} - Found in feed")
            # Get sample data
            dispatch_pattern = f'<Dispatch ID = "{code}">(.*?)</Dispatch>'
            dispatch_match = re.search(dispatch_pattern, response.text, re.S)
            if dispatch_match:
                dispatch_content = dispatch_match.group(1)
                log_pattern = r'<Log ID = "([^"]+)">'
                logs = re.findall(log_pattern, dispatch_content)
                print(f"      Has {len(logs)} log entries")
                if logs:
                    print(f"      Sample log IDs: {logs[:3]}")
        else:
            print(f"[X]  {code} - NOT found in feed")
            
except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
