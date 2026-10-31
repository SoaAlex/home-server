#!/usr/bin/env python3
"""Render each figure of infrastructure.html to <section-id>.png (2x) next to it.

Needs Playwright with Chromium. On the NAS, run it in a throwaway container:

    docker run --rm -v "$PWD":/w -w /w mcr.microsoft.com/playwright/python:v1.48.0-noble \
      sh -c 'pip install -q --break-system-packages playwright==1.48.0 && python3 render.py'
"""
import os

from playwright.sync_api import sync_playwright

here = os.path.dirname(os.path.abspath(__file__))
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=2)
    page.goto("file://" + os.path.join(here, "infrastructure.html"))
    page.wait_for_timeout(2000)  # web fonts
    for section in page.query_selector_all("section"):
        name = section.get_attribute("id")
        section.query_selector("svg").screenshot(path=os.path.join(here, f"{name}.png"))
        print("rendered", name)
    browser.close()
