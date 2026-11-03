"""Refresh the long-lived Threads token (valid 60 days; refreshing extends it).
Prints the new token to stdout so the workflow can save it back into the repo secret."""
import os
import sys

import requests

r = requests.get("https://graph.threads.net/refresh_access_token",
                 params={"grant_type": "th_refresh_token",
                         "access_token": os.environ["THREADS_ACCESS_TOKEN"]}, timeout=30)
data = r.json()
if "access_token" not in data:
    sys.exit(f"Refresh failed: {data}")
print(f"::add-mask::{data['access_token']}", file=sys.stderr)
print(f"Token refreshed, expires in {int(data.get('expires_in', 0)) // 86400} days", file=sys.stderr)
print(data["access_token"])
