#!/usr/bin/env python3
"""Test SACC dispatch code to see if it covers Barstow area."""

import requests
import re
from bs4 import BeautifulSoup

def clean_text(match):
    if match:
        return match.group(1).strip().strip('"')
    return ""

CHP_XML_FEED_URL = "http://media.chp.ca.gov/sa_xml/sa.xml"
DISPATCH_CODE = "SACC"

print(f"Fetching CHP XML feed to check {DISPATCH_CODE} dispatch...")
try:
    response = requests.get(CHP_XML_FEED_URL, timeout=30)
    
    # Extract SACC dispatch content
    dispatch_pattern = f'<Dispatch ID = "{DISPATCH_CODE}">(.*?)</Dispatch>'
    dispatch_match = re.search(dispatch_pattern, response.text, re.S)
    
    if dispatch_match:
        dispatch_content = dispatch_match.group(1)
        
        # Extract log blocks
        log_pattern = r'<Log ID = "([^"]+)">(.*?)</Log>'
        logs = re.findall(log_pattern, dispatch_content, re.S)
        
        print(f"Found {len(logs)} log entries in {DISPATCH_CODE}")
        
        # Parse a few sample logs to see locations
        print("\nSample incidents from SACC:")
        for log_id, log_body in logs[:5]:
            # Extract key fields
            log_time = clean_text(re.search(r'<LogTime>"?(.*?)"?</LogTime>', log_body, re.S))
            log_type = clean_text(re.search(r'<LogType>"?(.*?)"?</LogType>', log_body, re.S))
            location = clean_text(re.search(r'<Location>"?(.*?)"?</Location>', log_body, re.S))
            area = clean_text(re.search(r'<Area>"?(.*?)"?</Area>', log_body, re.S))
            
            print(f"\nLog ID: {log_id}")
            print(f"  Time: {log_time}")
            print(f"  Type: {log_type}")
            print(f"  Location: {location}")
            print(f"  Area: {area}")
            
            # Check if location mentions Barstow/Victorville area
            if any(keyword in (location + area).upper() for keyword in ['BARSTOW', 'VICTORVILLE', 'VV', 'BA', 'MOONGO']):
                print(f"  ** APPEARS TO BE IN BARSTOW AREA **")
    else:
        print(f"Could not find {DISPATCH_CODE} dispatch content")
        
except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
