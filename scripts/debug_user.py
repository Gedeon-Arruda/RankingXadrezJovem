import sys, json, requests, certifi
from urllib.parse import quote

USER_URL = "https://lichess.org/api/user/{}"
username = sys.argv[1] if len(sys.argv) > 1 else "gedevon_arrudev"
s = requests.Session(); s.verify = certifi.where()
resp = s.get(USER_URL.format(quote(username)), timeout=10)
print("status:", resp.status_code)
try:
    data = resp.json()
    print(json.dumps(data.get("profile", data), ensure_ascii=False, indent=2))
except Exception:
    print(resp.text)