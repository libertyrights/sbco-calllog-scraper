#!/usr/bin/env python3
"""Test all dispatch codes to find which covers Barstow/Inland area."""

import requests
import re

CHP_XML_FEED_URL = "http://media.chp.ca.gov/sa_xml/sa.xml"

def clean_text(match):
    if match:
        return match.group(1).strip().strip('"')
    return ""

print(f"Fetching CHP XML feed to check all dispatch areas...")
try:
    response = requests.get(CHP_XML_FEED_URL, timeout=30)
    
    # Get all available dispatch codes
    dispatch_pattern = r'<Dispatch ID = "([^"]+)">'
    all_dispatches = re.findall(dispatch_pattern, response.text)
    
    print(f"Found {len(all_dispatches)} dispatch codes")
    
    # Check each dispatch for Barstow/Victorville/Inland area mentions
    barstow_keywords = ['BARSTOW', 'VICTORVILLE', 'VV', 'BA', 'MOONGO', 'NEEDLES', 'INLAND', 'SAN BERNARDINO']
    
    for dispatch_code in all_dispatches:
        dispatch_pattern = f'<Dispatch ID = "{dispatch_code}">(.*?)</Dispatch>'
        dispatch_match = re.search(dispatch_pattern, response.text, re.S)
        
        if dispatch_match:
            dispatch_content = dispatch_match.group(1)
            
            # Extract log blocks
            log_pattern = r'<Log ID = "([^"]+)">(.*?)</Log>'
            logs = re.findall(log_pattern, dispatch_content, re.S)
            
            # Check if any logs mention Barstow area
            barstow_area_logs = 0
            sample_areas = set()
            
            for log_id, log_body in logs[:10]:  # Check first 10 logs per dispatch
                location = clean_text(re.search(r'<Location>"?(.*?)"?</Location>', log_body, re.S))
                area = clean_text(re.search(r'<Area>"?(.*?)"?</Area>', log_body, re.S))
                
                combined_text = (location + " " + area).upper()
                
                if any(keyword in combined_text for keyword in barstow_keywords):
                    barstow_area_logs += 1
                
                if area:
                    sample_areas.add(area)
            
            if barstow_area_logs > 0:
                print(f"\n[**POTENTIAL MATCH**] {dispatch_code}: {barstow_area_logs} Barstow-area logs out of {len(logs)} total")
                print(f"   Sample areas: {list(sample_areas)[:5]}")
            else:
                print(f"[{dispatch_code}]: {len(logs)} logs, sample areas: {list(sample_areas)[:3]}")
                
except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
