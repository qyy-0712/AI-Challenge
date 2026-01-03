import json
import urllib.request

url = "http://127.0.0.1:8000/review"
payload = {"repo_full_name": "qyy-0712/test", "pr_number": 3, "requirements": None}
req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type":"application/json"}, method="POST")
with urllib.request.urlopen(req, timeout=120) as resp:
    body = resp.read().decode("utf-8", errors="replace")
print(body[:600])
