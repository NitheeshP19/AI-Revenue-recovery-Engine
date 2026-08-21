#!/usr/bin/env python3
"""
===============================================================================
  AI Revenue Recovery System — Anti-Cold-Start Demo Warmer
  Author  : Senior DevOps & Site Reliability Engineer
  Version : 1.0.0
-------------------------------------------------------------------------------
  PURPOSE:
    Pings serverless host endpoints (Render, Neon DB health checks) every 4
    minutes to keep compute units active, eliminating cold starts before/during
    live hackathon demonstrations.
    
    Uses python's built-in urllib module for zero external dependency setup.
===============================================================================
"""

import time
import urllib.request
import urllib.error
from datetime import datetime
import sys

# Target endpoints for the live presentation. Update these to your actual Render/Neon URLs.
TARGET_URLS = [
    "https://go-api.onrender.com/health",
    "https://ml-service.onrender.com/health",
    "https://groq-agent.onrender.com/health"
]

# Pinging every 4 minutes (Neon pauses at 5m, Render spins down at 15m)
INTERVAL_SECONDS = 240

def get_timestamp() -> str:
    """Returns formatted current time, e.g., 10:45:02 AM"""
    return datetime.now().strftime("%I:%M:%S %p")

def ping_endpoint(url: str):
    """Pings a single health endpoint and outputs structured logs."""
    ts = get_timestamp()
    
    try:
        # Construct request with a user-agent header
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "DemoWarmupBot/1.0"}
        )
        
        # Dial url with a 10s timeout to prevent hanging connections
        with urllib.request.urlopen(req, timeout=10) as response:
            status_code = response.getcode()
            # In urllib, a successful response usually returns 200 OK
            print(f"[{ts}] GET {url} - Status: {status_code} OK", flush=True)
            
    except urllib.error.HTTPError as e:
        # Managed HTTP error (e.g. 500, 404, 401)
        print(f"[{ts}] GET {url} - Status: {e.code} {e.reason}", flush=True)
    except urllib.error.URLError as e:
        # Network unreachable or domain resolution error
        print(f"[{ts}] GET {url} - Status: FAILED (Reason: {e.reason})", flush=True)
    except Exception as e:
        # Any other unexpected exception (e.g. timeout)
        print(f"[{ts}] GET {url} - Status: ERROR (Reason: {str(e)})", flush=True)

def main():
    print("=" * 80)
    print("  AI REVENUE RECOVERY — LIVE DEMO WARMER")
    print(f"  Pinging {len(TARGET_URLS)} endpoint(s) every {INTERVAL_SECONDS // 60} minutes to prevent cold-starts.")
    print("=" * 80)
    for u in TARGET_URLS:
        print(f"  • {u}")
    print("-" * 80)
    print("Press Ctrl+C to terminate the pinger.\n", flush=True)
    
    try:
        while True:
            for url in TARGET_URLS:
                ping_endpoint(url)
            # Sleep until next check
            time.sleep(INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print(f"\n[{get_timestamp()}] Keep-alive pinger halted by user. Graceful exit.")
        sys.exit(0)

if __name__ == "__main__":
    main()
