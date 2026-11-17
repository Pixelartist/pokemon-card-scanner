#!/usr/bin/env python3
"""
Quickdraw matcher test suite.

Usage:
    python tests/test_quickdraw.py          # Run tests with daemon running
    python tests/test_quickdraw.py --daemon # Auto-restart daemon if needed

Tests:
    - Index loads with expected card count
    - English Wailmer self-match within MATCH_DIST
    - German Wailmer self-match within MATCH_DIST  
    - Language filtering prevents cross-language false positives
    - API endpoints respond correctly
"""

import sys
import os
import time
import argparse
import subprocess

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from services.quickdraw_matcher import QuickdrawMatcher

def run_daemon_if_needed():
    """Check if daemon is running, restart if not."""
    try:
        import requests
        r = requests.get('http://localhost:5005/api/quickdraw/status', timeout=2)
        return True
    except:
        print("Daemon not running, starting...")
        os.chdir('/opt/data/pokemon-card-scanner')
        subprocess.Popen(['nohup', '.venv/bin/python3', 'daemon.py'], 
                        stdout=open('/tmp/daemon.log', 'w'),
                        stderr=subprocess.STDOUT)
        time.sleep(5)
        return False

def test_index_loads():
    """Test that index loads with expected card count."""
    matcher = QuickdrawMatcher()
    assert matcher.load_index(), "Failed to load index"
    assert matcher.count == 40291, f"Expected 40291 cards, got {matcher.count}"
    print("✓ Index loads with 40,291 cards")
    return matcher

def test_english_self_match(matcher, match_dist):
    """Test English Wailmer self-match."""
    with open('data/images/tcgdex/en/me05-015.png', 'rb') as f:
        img = f.read()
    result = matcher.recognize(img, lang='en')
    assert result['decision'] == 'match', f"Expected match, got {result['decision']}"
    assert len(result['candidates']) > 0, "No candidates returned"
    top = result['candidates'][0]
    assert top['ptcg_id'] == 'me05-015', f"Expected me05-015, got {top['ptcg_id']}"
    assert top['distance'] < match_dist, f"Distance {top['distance']} >= MATCH_DIST {match_dist}"
    print(f"✓ English Wailmer self-match: distance {top['distance']:.3f} < {match_dist}")

def test_german_self_match(matcher, match_dist):
    """Test German Wailmer self-match."""
    with open('data/german_cards/images/de_me05-015.png', 'rb') as f:
        img = f.read()
    result = matcher.recognize(img, lang='de')
    assert result['decision'] == 'match', f"Expected match, got {result['decision']}"
    assert len(result['candidates']) > 0, "No candidates returned"
    top = result['candidates'][0]
    assert top['ptcg_id'] == 'de_me05-015', f"Expected de_me05-015, got {top['ptcg_id']}"
    assert top['distance'] < match_dist, f"Distance {top['distance']} >= MATCH_DIST {match_dist}"
    print(f"✓ German Wailmer self-match: distance {top['distance']:.3f} < {match_dist}")

def test_language_filter(matcher):
    """Test language filtering works correctly."""
    # English image with lang='en' should return English card
    with open('data/images/tcgdex/en/me05-015.png', 'rb') as f:
        img = f.read()
    result = matcher.recognize(img, lang='en')
    for c in result['candidates']:
        assert not c['ptcg_id'].startswith('de_'), \
            f"English query matched German card: {c['ptcg_id']}"
    print("✓ lang='en' filters to English cards only")

def test_api_endpoints():
    """Test API endpoints are accessible."""
    try:
        import requests
        # Status endpoint
        r = requests.get('http://localhost:5005/api/quickdraw/status', timeout=5)
        assert r.status_code == 200, f"Status endpoint failed: {r.status_code}"
        data = r.json()
        assert data['cards_indexed'] == 40291, f"Wrong card count: {data['cards_indexed']}"
        print("✓ /api/quickdraw/status returns 40,291 cards")
        
        # Scan endpoint - English
        with open('data/images/tcgdex/en/me05-015.png', 'rb') as f:
            img = f.read()
        r = requests.post('http://localhost:5005/api/scan', files={'image': img}, data={'lang': 'en'})
        assert r.status_code == 200, f"Scan endpoint failed: {r.status_code}"
        result = r.json()
        assert result['decision'] == 'match', f"Expected match, got {result['decision']}"
        print("✓ /api/scan works with English image (lang=en)")
        
        # Scan endpoint - German
        with open('data/german_cards/images/de_me05-015.png', 'rb') as f:
            img = f.read()
        r = requests.post('http://localhost:5005/api/scan', files={'image': img}, data={'lang': 'de'})
        assert r.status_code == 200, f"Scan endpoint failed: {r.status_code}"
        result = r.json()
        assert result['decision'] == 'match', f"Expected match, got {result['decision']}"
        print("✓ /api/scan works with German image (lang=de)")
        
    except ImportError:
        print("⚠ requests not available, skipping API tests")

def main():
    parser = argparse.ArgumentParser(description='Quickdraw matcher tests')
    parser.add_argument('--daemon', action='store_true', help='Auto-restart daemon if needed')
    args = parser.parse_args()
    
    print("Running Quickdraw Matcher Tests")
    print("=" * 40)
    
    if args.daemon:
        run_daemon_if_needed()
    
    os.chdir('/opt/data/pokemon-card-scanner')
    
    matcher = test_index_loads()
    match_dist = matcher.MATCH_DIST
    test_english_self_match(matcher, match_dist)
    test_german_self_match(matcher, match_dist)
    test_language_filter(matcher)
    test_api_endpoints()
    
    print("=" * 40)
    print("All tests passed!")

if __name__ == '__main__':
    main()