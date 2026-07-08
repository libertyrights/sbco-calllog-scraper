#!/usr/bin/env python3
"""Test if 'Inland' communications center covers Barstow area in XML feed."""

import requests
import re

CHP_XML_FEED_URL = "http://media.chp.ca.gov/sa_xml/sa.xml"

def clean_text(match):
    if match:
        return match.group(1).strip().strip('"')
    return ""

print(f"Fetching CHP XML feed to check for Inland dispatch...")
try:
    response = requests.get(CHP_XML_FEED_URL, timeout=30)
    
    # Check if any dispatch code might represent "Inland"
    dispatch_pattern = r'<Dispatch ID = "([^"]+)">'
    all_dispatches = re.findall(dispatch_pattern, response.text)
    
    print(f"Available dispatch codes: {all_dispatches}")
    
    # Check each dispatch for any that might be Inland-related
    inland_keywords = ['INLAND', 'SAN BERNARDINO', 'RIVERSIDE', 'ONTARIO', 'BARRSTOW', 'VICTORVILLE']
    
    for dispatch_code in all_dispatches:
        dispatch_pattern = f'<Dispatch ID = "{dispatch_code}">(.*?)</Dispatch>'
        dispatch_match = re.search(dispatch_pattern, response.text, re.S)
        
        if dispatch_match:
            dispatch_content = dispatch_match.group(1)
            
            # Extract log blocks
            log_pattern = r'<Log ID = "([^"]+)">(.*?)</Log>'
            logs = re.findall(log_pattern, dispatch_content, re.S)
            
            # Check if any logs mention Inland empire areas
            inland_area_logs = 0
            sample_areas = set()
            
            for log_id, log_body in logs[:20]:  # Check first 20 logs per dispatch
                location = clean_text(re.search(r'<Location>"?(.*?)"?</Location>', log_body, re.S))
                area = clean_text(re.search(r'<Area>"?(.*?)"?</Area>', log_body, re.S))
                
                combined_text = (location + " " + area).upper()
                
                if any(keyword in combined_text for keyword in inland_keywords):
                    inland_area_logs += 1
                
                if area:
                    sample_areas.add(area)
            
            if inland_area_logs > 0:
                print(f"\n[**INLAND EMPIRE AREA**] {dispatch_code}: {inland_area_logs} Inland-area logs out of {len(logs)} total")
                print(f"   Sample areas: {list(sample_areas)[:10]}")
                
                # Show some sample incidents
                print(f"   Sample incidents:")
                for log_id, log_body in logs[:3]:
                    location = clean_text(re.search(r'<Location>"?(.*?)"?</Location>', log_body, re.S))
                    area = clean_text(re.search(r'<Area>"?(.*?)"?</Area>', log_body, re.S))
                    log_type = clean_text(re.search(r'<LogType>"?(.*?)"?</LogType>', log_body, re.S))
                    print(f"     {log_id}: {area} - {location} - {log_type}")
                
except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
