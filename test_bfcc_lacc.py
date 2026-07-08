#!/usr/bin/env python3
"""Test BFCC and LACC dispatch codes more thoroughly."""

import requests
import re

CHP_XML_FEED_URL = "http://media.chp.ca.gov/sa_xml/sa.xml"

def clean_text(match):
    if match:
        return match.group(1).strip().strip('"')
    return ""

print(f"Fetching CHP XML feed to check BFCC and LACC...")
try:
    response = requests.get(CHP_XML_FEED_URL, timeout=30)
    
    # Test BFCC
    dispatch_pattern = f'<Dispatch ID = "BFCC">(.*?)</Dispatch>'
    dispatch_match = re.search(dispatch_pattern, response.text, re.S)
    
    if dispatch_match:
        dispatch_content = dispatch_match.group(1)
        log_pattern = r'<Log ID = "([^"]+)">(.*?)</Log>'
        logs = re.findall(log_pattern, dispatch_content, re.S)
        
        print(f"BFCC: {len(logs)} logs")
        sample_areas = set()
        for log_id, log_body in logs[:10]:
            area = clean_text(re.search(r'<Area>"?(.*?)"?</Area>', log_body, re.S))
            if area:
                sample_areas.add(area)
        print(f"Sample areas: {list(sample_areas)}")
        
        # Show sample incidents
        print("Sample BFCC incidents:")
        for log_id, log_body in logs[:3]:
            location = clean_text(re.search(r'<Location>"?(.*?)"?</Location>', log_body, re.S))
            area = clean_text(re.search(r'<Area>"?(.*?)"?</Area>', log_body, re.S))
            log_type = clean_text(re.search(r'<LogType>"?(.*?)"?</LogType>', log_body, re.S))
            print(f"  {log_id}: {area} - {location} - {log_type}")
    else:
        print("BFCC: No content found")
    
    # Test LACC more thoroughly
    dispatch_pattern = f'<Dispatch ID = "LACC">(.*?)</Dispatch>'
    dispatch_match = re.search(dispatch_pattern, response.text, re.S)
    
    if dispatch_match:
        dispatch_content = dispatch_match.group(1)
        log_pattern = r'<Log ID = "([^"]+)">(.*?)</Log>'
        logs = re.findall(log_pattern, dispatch_content, re.S)
        
        print(f"\nLACC: {len(logs)} logs")
        sample_areas = set()
        for log_id, log_body in logs[:20]:
            area = clean_text(re.search(r'<Area>"?(.*?)"?</Area>', log_body, re.S))
            if area:
                sample_areas.add(area)
        print(f"Sample areas: {sorted(list(sample_areas))}")
        
        # Look for Inland Empire areas
        inland_keywords = ['ANTELOPE', 'PALMDALE', 'LANCASTER', 'VICTORVILLE', 'BARSTOW', 'SAN BERNARDINO']
        inland_logs = []
        
        for log_id, log_body in logs:
            location = clean_text(re.search(r'<Location>"?(.*?)"?</Location>', log_body, re.S))
            area = clean_text(re.search(r'<Area>"?(.*?)"?</Area>', log_body, re.S))
            combined = (location + " " + area).upper()
            
            if any(keyword in combined for keyword in inland_keywords):
                inland_logs.append((log_id, area, location))
        
        if inland_logs:
            print(f"\nFound {len(inland_logs)} Inland Empire area logs in LACC:")
            for log_id, area, location in inland_logs[:5]:
                print(f"  {log_id}: {area} - {location}")
        else:
            print("\nNo Inland Empire area logs found in LACC")
                
except Exception as e:
    import traceback
    print(f"Error: {e}")
    traceback.print_exc()
