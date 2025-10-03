# players_generator.py
import json
import time
import requests
import certifi
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import quote

# ---------- sua parte existente (mantive praticamente igual) ----------
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
DEFAULT_OUT_FILE = f"{OUT_DIR}/players.json"


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
    name = profile.get("name") or profile.get("fullName") or ""
    first = profile.get("firstName") or profile.get("first") or ""
    last = profile.get("lastName") or profile.get("last") or ""
    if first and last:
        name = f"{first} {last}".strip()
    elif first and not name:
        name = first.strip()
    if not name:
        name = profile.get("realName") or profile.get("displayName") or ""
    if not name and isinstance(uobj, dict):
        name = uobj.get("name") or uobj.get("fullName") or uobj.get("displayName") or ""
    return (name or "").strip()


def fetch_rating_history(session, username):
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
        history_diffs = fetch_rating_history(session, username)
        return {
            "username": username,
            "name": name,
            "profile": profile_url,
            "blitz": blitz,
            "bullet": bullet,
            "rapid": rapid,
            "seenAt": seenAt,
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


def rating_status(diff):
    if diff is None:
        return None
    try:
        d = int(diff)
    except Exception:
        return None
    if d > 0:
        return "subiu"
    if d < 0:
        return "caiu"
    return "manteve"


def generate_players_json(output_path=DEFAULT_OUT_FILE, write_file=True):
    """
    Coleta dados, monta o payload e grava em `output_path` quando write_file==True.
    Retorna o dicionário final (out).
    """
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

    # load previous snapshot only if we will write to the same file
    prev_map = {}
    prev_rank_map = {}
    try:
        if write_file and os.path.exists(output_path):
            with open(output_path, "r", encoding="utf-8") as f:
                prev = json.load(f)
            for idx, pp in enumerate((prev.get("players") or [])):
                uname = (pp.get("username") or "").strip().lower()
                if uname:
                    prev_map[uname] = pp
                    prev_rank_map[uname] = idx + 1
    except Exception:
        pass

    for p in active_sorted:
        if not (p.get("name") or "").strip():
            p["name"] = "Sem nome registrado"
        key = (p.get("username") or "").strip().lower()
        prev = prev_map.get(key)

        if prev:
            try:
                p["blitz_diff"] = int(p.get("blitz") or 0) - int(prev.get("blitz") or 0)
            except Exception:
                p["blitz_diff"] = 0
            try:
                p["bullet_diff"] = int(p.get("bullet") or 0) - int(prev.get("bullet") or 0)
            except Exception:
                p["bullet_diff"] = 0
            try:
                p["rapid_diff"] = int(p.get("rapid") or 0) - int(prev.get("rapid") or 0)
            except Exception:
                p["rapid_diff"] = 0
        else:
            p["blitz_diff"] = p.get("recent_blitz_diff") if p.get("recent_blitz_diff") is not None else 0
            p["bullet_diff"] = p.get("recent_bullet_diff") if p.get("recent_bullet_diff") is not None else 0
            p["rapid_diff"] = p.get("recent_rapid_diff") if p.get("recent_rapid_diff") is not None else 0

        p.pop("recent_blitz_diff", None)
        p.pop("recent_bullet_diff", None)
        p.pop("recent_rapid_diff", None)

    for idx, p in enumerate(active_sorted):
        current_pos = idx + 1
        p["position"] = current_pos
        key = (p.get("username") or "").strip().lower()
        prev_pos = prev_rank_map.get(key)
        if prev_pos is None:
            p["position_change"] = None
            p["position_arrow"] = None
        else:
            change = prev_pos - current_pos
            p["position_change"] = change
            if change > 0:
                p["position_arrow"] = "▲"
            elif change < 0:
                p["position_arrow"] = "▼"
            else:
                p["position_arrow"] = "→"
        p["blitz_status"] = rating_status(p.get("blitz_diff"))
        p["bullet_status"] = rating_status(p.get("bullet_diff"))
        p["rapid_status"] = rating_status(p.get("rapid_diff"))

    out = {
        "generated_at": int(time.time()*1000),
        "count": len(active_sorted),
        "players": active_sorted
    }

    if write_file:
        out_dir = os.path.dirname(output_path) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"Wrote {output_path} ({len(active_sorted)} players)")
    else:
        # print to stdout
        print(json.dumps(out, ensure_ascii=False, indent=2))

    return out
# ---------- fim parte existente ----------


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Gerador de docs/players.json para o XadrezJovemes (sem Flask).")
    parser.add_argument("--output", "-o", default=DEFAULT_OUT_FILE, help="Caminho do arquivo de saída (default: docs/players.json)")
    parser.add_argument("--stdout", action="store_true", help="Imprime o JSON no stdout em vez de gravar o arquivo")
    args = parser.parse_args()

    try:
        generate_players_json(output_path=args.output, write_file=not args.stdout)
    except Exception as e:
        print("Erro gerando players.json:", e)
        raise
