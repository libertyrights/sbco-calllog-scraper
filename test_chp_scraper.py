#!/usr/bin/env python3
"""Test the CHP scraper directly to see if it's working."""

import sys
sys.path.insert(0, r'C:\Users\mark\Documents\Haas\sbco-calllog-scraper')

from chp_scraper import scrape_chp_incidents

print("Testing CHP scraper...")
incidents = scrape_chp_incidents()
print(f"Found {len(incidents)} CHP incidents")

if incidents:
    print("\nSample incident:")
    for key, value in incidents[0].items():
        print(f"{key}: {value}")
