#!/usr/bin/env python3
"""Debug: test hash matching with proper image loading.

The `stored`/`exact` fields only mean anything when the page fell back to
card-hashes.json: the packed blob (card-hashes.bin) keeps no hash strings, so
`stored` reads 'missing' and the comparison is false in the normal case.
"""
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

# Playwright's own Chromium is the default. Set CHROME_PATH only to pin a
# different binary (a system Chrome, or a browser dir Playwright does not manage).
CHROME = os.environ.get("CHROME_PATH") or None
PAGE = "http://localhost:5005/"

with sync_playwright() as p:
    # executable_path=None is Playwright's default browser.
    b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    page = b.new_page()
    page.goto(PAGE, wait_until="load")
    page.wait_for_function("window.__cardScanner && window.__cardScanner.ready === true", timeout=60000)
    print("browser scanner ready")
    
    # Test with proper async loading
    result = page.evaluate("""() => {
        const m = window.__cardScanner.matcher;
        const storedHash = (m.cardHashes.get('xy1-1') || {}).hash || 'missing';
        
        return new Promise((resolve) => {
            const img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = async () => {
                const browserHash = await m.calculateHash(img);
                resolve({
                    browser: browserHash.substring(0, 40),
                    stored: storedHash.substring(0, 40),
                    exact: browserHash === storedHash,
                    browserLen: browserHash.length,
                    storedLen: storedHash.length,
                });
            };
            img.onerror = () => resolve({ error: 'img.onerror' });
            img.src = '/static/card_images/official/en/xy1-1.png';
        });
    }""")
    print(f"xy1-1: {json.dumps(result, indent=2)}")
    
    # Test xy1-1
    result2 = page.evaluate("""() => {
        const m = window.__cardScanner.matcher;
        const storedHash = (m.cardHashes.get('xy1-1') || {}).hash || 'missing';
        
        return new Promise((resolve) => {
            const img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = async () => {
                const browserHash = await m.calculateHash(img);
                resolve({
                    browser: browserHash.substring(0, 40),
                    stored: storedHash.substring(0, 40),
                    exact: browserHash === storedHash,
                });
            };
            img.onerror = () => resolve({ error: 'img.onerror' });
            img.src = '/static/card_images/official/en/xy1-1.png';
        });
    }""")
    print(f"xy1-1: {json.dumps(result2, indent=2)}")
    
    # Test full matching: detect + identify on xy1-1
    test_identify = page.evaluate("""() => {
        return window.__cardScanner.tryIdentify(
            document.querySelector('#camera') || document.createElement('canvas')
        ).catch(e => ({ error: e.message }));
    }""")
    # This won't work without a real camera, so let's use a loaded image instead
    test_identify2 = page.evaluate("""() => {
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.src = '/static/card_images/official/en/xy1-1.png';
        return new Promise((resolve) => {
            img.onload = async () => {
                const det = await window.__cardScanner.detect(img);
                if (!det) return { detection: null };
                
                const { cropCardStraight } = await import('/static/js/card-detector-tfjs.js');
                const crop = cropCardStraight(img, det);
                const matches = await window.__cardScanner.matcher.match(crop, 85, 5);
                resolve({
                    detection_conf: det.confidence,
                    matches_count: matches.length,
                    top_match: matches[0] ? { id: matches[0].id, sim: matches[0].similarity } : null,
                });
            };
            img.onerror = () => resolve({ error: 'img.onerror' });
        });
    }""")
    print(f"tryIdentify(xy1-1): {json.dumps(test_identify2, indent=2)}")
    
    b.close()