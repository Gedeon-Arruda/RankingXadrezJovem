import json
import time
import requests
import certifi
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import quote
import os

TEAM_ID = "xadrezjovemes"
TEAM_URL = "https://lichess.org/api/team/{}/users"
USER_URL = "https://lichess.org/api/user/{}"
MAX_WORKERS = 8
REQUEST_TIMEOUT = 10
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
    s.verify = certifi.where()
    return s

def fetch_team_members(session):
    url = TEAM_URL.format(quote(TEAM_ID))
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    text = resp.text.strip()
    users = []
    try:
        if text.startswith('['):
            arr = resp.json()
            for obj in arr:
                if isinstance(obj, dict):
                    users.append(obj.get('id') or obj.get('username'))
        else:
            for line in text.splitlines():
                if not line.strip(): continue
                try:
                    obj = json.loads(line)
                    users.append(obj.get('id') or obj.get('username'))
                except Exception:
                    users.append(line.strip())
    except Exception:
        pass
    return [u for u in users if u]

def extract_name_from_profile(profile, user_obj):
    if not profile and not user_obj:
        return ""
    profile = profile or {}
    # tenta várias chaves possíveis
    candidates = []
    # campos comuns
    for k in ("name","fullName","full_name","displayName","display_name"):
        v = profile.get(k)
        if v: candidates.append(str(v).strip())
    # first/last combinados
    first = profile.get("firstName") or profile.get("first") or profile.get("givenName") or ""
    last = profile.get("lastName") or profile.get("last") or profile.get("familyName") or ""
    if first and last:
        candidates.append(f"{first} {last}".strip())
    elif first:
        candidates.append(first.strip())
    # fallback para campos de topo do objeto user
    for k in ("name","fullName","full_name"):
        v = user_obj.get(k) if isinstance(user_obj, dict) else None
        if v: candidates.append(str(v).strip())
    # remove vazios e duplicates
    seen = set()
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            return c
    return ""

def fetch_user(session, username):
    try:
        resp = session.get(USER_URL.format(quote(username)), timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        u = resp.json()
        prof = u.get("profile") or {}
        name = extract_name_from_profile(prof, u)
        perfs = u.get("perfs", {})
        blitz = perfs.get("blitz", {}).get("rating")
        bullet = perfs.get("bullet", {}).get("rating")
        rapid = perfs.get("rapid", {}).get("rating")
        seenAt = u.get("seenAt") or u.get("lastSeenAt") or u.get("seenAtMillis") or None
        profile_url = prof.get("url") or f"https://lichess.org/@/{username}"
        return {
            "username": username,
            "name": name or "",
            "profile": profile_url,
            "blitz": blitz,
            "bullet": bullet,
            "rapid": rapid,
            "seenAt": seenAt
        }
    except Exception as e:
        print(f"warning: erro ao buscar {username}: {e}")
        return None

def active_since_days(player, days=ACTIVE_DAYS):
    if not player: return False
    seen = player.get("seenAt")
    if not seen: return False
    try:
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
    # dedupe por username, preferir ratings/seenAt mais recentes
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
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote {OUT_FILE} ({len(active_sorted)} players)")
    print("Arquivo gerado. Para publicar, execute:")
    print("  git add docs/players.json")
    print("  git commit -m \"chore: atualiza players.json (inclui nome real)\"")
    print("  git pull --rebase origin main && git push origin main")

if __name__ == "__main__":
    main()