#!/usr/bin/env python3
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=3) as response:
    if response.status != 200 or not isinstance(json.load(response), dict):
        raise SystemExit(1)
