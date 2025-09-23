import os
import json
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import quote
import certifi

TEAM_ID = "xadrezjovemes"
TEAM_URL = "https://lichess.org/api/team/{}/users"
USER_URL = "https://lichess.org/api/user/{}"
MAX_WORKERS = 8
REQUEST_TIMEOUT = 8
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.0
ACTIVE_DAYS = 30
OUT_DIR = "docs"
OUT_FILE = f"{OUT_DIR}/players.json"

def make_session():
    s = requests.Session()
    retries = Retry(total=RETRY_ATTEMPTS, backoff_factor=RETRY_BACKOFF, status_forcelist=(500,502,503,504))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({"User-Agent":"xadrezjovemes-generator/1.0"})
    # força requests a usar o bundle do certifi (resolve CERTIFICATE_VERIFY_FAILED)
    s.verify = certifi.where()
    return s

def fetch_team_members(session):
    url = TEAM_URL.format(quote(TEAM_ID))
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    # API returns newline-delimited JSON per user; parse usernames
    users = []
    for line in resp.text.splitlines():
        if not line.strip(): continue
        try:
            obj = json.loads(line)
            if 'id' in obj:
                users.append(obj['id'])
            elif 'username' in obj:
                users.append(obj['username'])
        except Exception:
            continue
    return users

def fetch_user(session, username):
    try:
        resp = session.get(USER_URL.format(quote(username)), timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        u = resp.json()
        # extração segura de nome real
        name = ""
        prof = u.get("profile") or {}
        first = prof.get("firstName") or prof.get("name") or ""
        last = prof.get("lastName") or ""
        if first and last:
            name = f"{first} {last}".strip()
        elif first:
            name = first
        elif prof.get("name"):
            name = prof.get("name")
        # ratings
        perfs = u.get("perfs", {})
        blitz = perfs.get("blitz", {}).get("rating")
        bullet = perfs.get("bullet", {}).get("rating")
        rapid = perfs.get("rapid", {}).get("rating")
        seenAt = u.get("seenAt") or u.get("lastSeenAt") or u.get("activity") or None
        profile_url = prof.get("url") or f"https://lichess.org/@/{username}"
        return {
            "username": username,
            "name": name,
            "profile": profile_url,
            "blitz": blitz,
            "bullet": bullet,
            "rapid": rapid,
            "seenAt": seenAt
        }
    except Exception as e:
        # falha isolada não interrompe todo o processo
        print(f"warning: erro ao buscar {username}: {e}")
        return None

def active_since_days(player, days=ACTIVE_DAYS):
    if not player: return False
    seen = player.get("seenAt")
    if not seen: return False
    try:
        # seenAt costuma vir em ms epoch
        ts = int(seen)
    except Exception:
        try:
            ts = int(float(seen))
        except Exception:
            return False
    age_days = (time.time()*1000 - ts) / (24*3600*1000)
    return age_days <= days

def main():
    session = make_session()
    print("Fetching team members...")
    members = fetch_team_members(session)
    print(f"Members: {len(members)}")
    players = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_user, session, m): m for m in members}
        for fut in as_completed(futures):
            res = fut.result()
            if res:
                players.append(res)
    # dedupe by username, preferring entries with more ratings / recent seenAt
    byu = {}
    def score(p):
        return (p.get("blitz") or 0) + (p.get("bullet") or 0) + (p.get("rapid") or 0)
    for p in players:
        key = (p.get("username") or "").strip().lower()
        if not key:
            continue
        if key not in byu or score(p) > score(byu[key]) or (p.get("seenAt") or 0) > (byu[key].get("seenAt") or 0):
            byu[key] = p
    active = [v for v in byu.values() if active_since_days(v)]
    active_sorted = sorted(active, key=lambda x: (-(x.get("blitz") or 0), -(x.get("bullet") or 0), -(x.get("rapid") or 0)))
    out = {
        "generated_at": int(time.time()*1000),
        "count": len(active_sorted),
        "players": active_sorted
    }
    # write file
    import os
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote {OUT_FILE} ({len(active_sorted)} players)")
    # Não realizar git push automático. Faça commit/push manualmente:
    print("Arquivo gerado. Para publicar, execute:")
    print("  git add docs/players.json docs/index.html")
    print("  git commit -m \"chore: atualiza players.json e frontend\"")
    print("  git pull --rebase origin main   # integre remoto se necessário")
    print("  git push origin main")

if __name__ == "__main__":
    main()