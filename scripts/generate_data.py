import os, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests
from urllib.parse import quote

TEAM_ID = "xadrezjovemes"
TEAM_URL = "https://lichess.org/api/team/{}/users"
USER_URL = "https://lichess.org/api/user/{}"
MAX_WORKERS = 8
REQUEST_TIMEOUT = 8
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.0
ACTIVE_DAYS = 30
OUT_DIR = "docs"
OUT_FILE = os.path.join(OUT_DIR, "players.json")

def create_session():
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=(429,500,502,503,504), allowed_methods=("GET",))
    adapter = HTTPAdapter(max_retries=retries, pool_maxsize=25)
    s.mount("https://", adapter); s.mount("http://", adapter)
    s.headers.update({"User-Agent":"xadrezjovemes-ranking-generator/1.0"})
    return s

def safe_int(v):
    try: return int(v)
    except:
        try: return int(float(v))
        except: return 0

def get_team_members(session):
    url = TEAM_URL.format(TEAM_ID)
    resp = session.get(url, stream=True, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    users=[]; seen=set()
    for line in resp.iter_lines():
        if not line: continue
        try:
            obj = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        username = obj.get("id") or obj.get("username") or (obj.get("user") or {}).get("id")
        if username and username not in seen:
            seen.add(username); users.append(username)
    return users

def fetch_user_once(session, username, timeout):
    try:
        resp = session.get(USER_URL.format(username), timeout=timeout)
        if resp.status_code != 200: return None
        data = resp.json()
        if not isinstance(data, dict): return None
        perfs = data.get("perfs",{}) or {}
        blitz = perfs.get("blitz",{}).get("rating")
        if blitz is None: return None
        blitz = safe_int(blitz)
        if blitz <= 0: return None
        bullet = safe_int(perfs.get("bullet",{}).get("rating") or 0)
        rapid = safe_int(perfs.get("rapid",{}).get("rating") or 0)
        seen_at = safe_int(data.get("seenAt") or data.get("seen_at") or 0)
        return {
            "username": username,
            "blitz": blitz,
            "bullet": bullet,
            "rapid": rapid,
            "seenAt": seen_at,
            "profile": f"https://lichess.org/@/{quote(username)}"
        }
    except Exception:
        return None

def fetch_user_with_retries(session, username):
    timeout = REQUEST_TIMEOUT
    for attempt in range(1, RETRY_ATTEMPTS+1):
        res = fetch_user_once(session, username, timeout=timeout)
        if res is not None: return res
        time.sleep(RETRY_BACKOFF * attempt)
        timeout = min(timeout * 1.5, REQUEST_TIMEOUT*2)
    return None

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    with create_session() as session:
        members = get_team_members(session)
        print(f"Members: {len(members)}")
        players=[]; failed=[]
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(members)))) as exe:
            futures = {exe.submit(fetch_user_with_retries, session, u): u for u in members}
            for fut in as_completed(futures):
                res = fut.result()
                if res is None:
                    failed.append(futures[fut])
                else:
                    players.append(res)
        # optional retries for failed
        for u in failed:
            r = fetch_user_with_retries(session, u)
            if r: players.append(r)
        # filter active
        now_ms = int(time.time()*1000)
        cutoff_ms = now_ms - ACTIVE_DAYS*24*3600*1000
        active = [p for p in players if p.get("seenAt",0) >= cutoff_ms]
        active_sorted = sorted(active, key=lambda x: x.get("blitz",0), reverse=True)
        out = {"generated_at": now_ms, "count": len(active_sorted), "players": active_sorted}
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"Wrote {OUT_FILE} ({len(active_sorted)} players)")
        os.system('git add docs/index.html docs/players.json scripts/generate_data.py ranking.py .github/workflows/generate.yml')
        os.system('git commit -m "Atualiza frontend e gera players.json"')
        os.system('git push origin main')

if __name__ == "__main__":
    main()