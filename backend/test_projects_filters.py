#!/usr/bin/env python3
"""
Manual/quick test for GET /api/projects with filters.
Run from backend: python test_projects_filters.py
Requires: backend running, PARSEHUB_API_KEY in env (or .env).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("BACKEND_URL", "http://localhost:5000")
API_KEY = os.getenv("PARSEHUB_API_KEY", "")


def req(params):
    p = dict(params)
    if API_KEY:
        p["api_key"] = API_KEY
    r = requests.get(f"{BASE_URL}/api/projects", params=p, timeout=15)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text


def main():
    print("\n" + "=" * 60)
    print("GET /api/projects — filter tests")
    print("=" * 60)
    if not API_KEY:
        print("Warning: PARSEHUB_API_KEY not set; request may return 400/401.")
    print()

    tests = [
        ("No filters", {"page": 1, "limit": 50}),
        ("Region only", {"page": 1, "limit": 50, "region": "APAC"}),
        ("Country only", {"page": 1, "limit": 50, "country": "Germany"}),
        ("Brand only", {"page": 1, "limit": 50, "brand": "TestBrand"}),
        ("Region + country", {"page": 1, "limit": 50, "region": "EMENA", "country": "Germany"}),
    ]

    ok = 0
    for name, params in tests:
        print(f"  {name}: params={params}")
        try:
            code, data = req(params)
            if code == 200:
                if isinstance(data, dict) and data.get("success"):
                    proj_count = len(data.get("by_project", data.get("projects", [])))
                    total = data.get("pagination", {}).get("total", data.get("project_count", "?"))
                    print(f"    -> 200 OK (projects: {proj_count}, total: {total})")
                    ok += 1
                else:
                    print(f"    -> 200 but success=False or unexpected shape: {list(data.keys())[:8]}")
            else:
                print(f"    -> {code} {data if isinstance(data, str) else data.get('error', data)}")
        except Exception as e:
            print(f"    -> Exception: {e}")
        print()

    print("=" * 60)
    print(f"Passed: {ok}/{len(tests)}")
    print("=" * 60)
    return 0 if ok == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
