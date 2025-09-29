import json
import time
import requests
import certifi
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import quote

TEAM_ID = "xadrezjovemes"
TEAM_URL = "https://lichess.org/api/team/{}/users"
USER_URL = "https://lichess.org/api/user/{}"
USER_RATING_HISTORY_URL = "https://lichess.org/api/user/{}/rating-history"
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
                    users.append(obj.get('id') or obj.get('username') or obj.get('name'))
                except Exception:
                    users.append(line.strip())
    except Exception:
        pass
    return [u for u in users if u]

def extract_name_from_profile(profile, uobj):
    if not profile and not isinstance(uobj, dict):
        return ""
    profile = profile or {}
    # Prioridade: profile.name, profile.fullName, profile.first/last, top-level name/displayName
    name = profile.get("name") or profile.get("fullName") or ""
    first = profile.get("firstName") or profile.get("first") or ""
    last = profile.get("lastName") or profile.get("last") or ""
    if first and last:
        name = f"{first} {last}".strip()
    elif first and not name:
        name = first.strip()
    # some users use human name in 'bio' or 'realName' (rare)
    if not name:
        name = profile.get("realName") or profile.get("displayName") or ""
    if not name and isinstance(uobj, dict):
        name = uobj.get("name") or uobj.get("fullName") or uobj.get("displayName") or ""
    return (name or "").strip()

def fetch_rating_history(session, username):
    """Retorna dict { 'blitz': diff|null, 'bullet': diff|null, 'rapid': diff|null } a partir do rating-history"""
    out = {'blitz': None, 'bullet': None, 'rapid': None}
    try:
        resp = session.get(USER_RATING_HISTORY_URL.format(quote(username)), timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return out
        arr = resp.json()
        for rec in arr:
            name = (rec.get('name') or '').lower()
            if name not in out: continue
            pts = rec.get('points') or []
            # pts = [[ts, rating], ...]
            if not pts: 
                out[name] = None
            else:
                if len(pts) >= 2:
                    last = pts[-1][1]
                    prev = pts[-2][1]
                    try:
                        out[name] = int(last) - int(prev)
                    except Exception:
                        out[name] = None
                else:
                    # apenas um ponto no histórico: não há diffs recentes calculáveis
                    out[name] = None
    except Exception:
        return out
    return out

def fetch_user(session, username):
    try:
        resp = session.get(USER_URL.format(quote(username)), timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404: return None
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

        # tenta obter diffs diretamente do rating-history (preferência: corresponde ao que aparece no perfil)
        history_diffs = fetch_rating_history(session, username)

        return {
            "username": username,
            "name": name,
            "profile": profile_url,
            "blitz": blitz,
            "bullet": bullet,
            "rapid": rapid,
            "seenAt": seenAt,
            # campos auxiliares: diffs calculados a partir do rating-history (padrão None se não disponível)
            "recent_blitz_diff": history_diffs.get('blitz'),
            "recent_bullet_diff": history_diffs.get('bullet'),
            "recent_rapid_diff": history_diffs.get('rapid'),
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
        try: ts = int(float(seen))
        except Exception: return False
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
            if res: players.append(res)
    # dedupe
    byu = {}
    def score(p): return (p.get("blitz") or 0) + (p.get("bullet") or 0) + (p.get("rapid") or 0)
    for p in players:
        key = (p.get("username") or "").strip().lower()
        if not key: continue
        if key not in byu or score(p) > score(byu[key]) or (p.get("seenAt") or 0) > (byu[key].get("seenAt") or 0):
            byu[key] = p
    active = [v for v in byu.values() if active_since_days(v)]
    active_sorted = sorted(active, key=lambda x: (-(x.get("blitz") or 0), -(x.get("bullet") or 0), -(x.get("rapid") or 0)))

    # carregar snapshot anterior para calcular diffs
    prev_map = {}
    try:
        if os.path.exists(OUT_FILE):
            with open(OUT_FILE, "r", encoding="utf-8") as f:
                prev = json.load(f)
            for pp in (prev.get("players") or []):
                uname = (pp.get("username") or "").strip().lower()
                if uname:
                    prev_map[uname] = pp
    except Exception:
        pass

    # preencher nome padrão e calcular diffs
    for p in active_sorted:
        if not (p.get("name") or "").strip():
            p["name"] = "Sem nome registrado"
        key = (p.get("username") or "").strip().lower()
        prev = prev_map.get(key)
        def calc_diff(curr, prevv):
            try:
                if prevv is None: return None
                return int(curr or 0) - int(prevv or 0)
            except Exception:
                return None

        # preferir diffs do rating-history (campo recent_*), senão fallback para snapshot anterior
        if p.get("recent_blitz_diff") is not None:
            p["blitz_diff"] = p.get("recent_blitz_diff")
        else:
            p["blitz_diff"] = calc_diff(p.get("blitz"), prev.get("blitz") if prev else None)

        if p.get("recent_bullet_diff") is not None:
            p["bullet_diff"] = p.get("recent_bullet_diff")
        else:
            p["bullet_diff"] = calc_diff(p.get("bullet"), prev.get("bullet") if prev else None)

        if p.get("recent_rapid_diff") is not None:
            p["rapid_diff"] = p.get("recent_rapid_diff")
        else:
            p["rapid_diff"] = calc_diff(p.get("rapid"), prev.get("rapid") if prev else None)

        # remover campos auxiliares antes de salvar (opcional)
        p.pop("recent_blitz_diff", None)
        p.pop("recent_bullet_diff", None)
        p.pop("recent_rapid_diff", None)
    
    out = {
        "generated_at": int(time.time()*1000),
        "count": len(active_sorted),
        "players": active_sorted
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Wrote {OUT_FILE} ({len(active_sorted)} players)")

if __name__ == "__main__":
    main()