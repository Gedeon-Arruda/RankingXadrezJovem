import requests
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, jsonify, request, render_template
from urllib.parse import quote

TEAM_ID = "xadrezjovemes"
TEAM_URL = "https://lichess.org/api/team/{}/users"
USER_URL = "https://lichess.org/api/user/{}"

MAX_WORKERS = 8
ACTIVE_DAYS = 30
REQUEST_TIMEOUT = 8
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.0

app = Flask(__name__)
PLAYERS = []
DATA_LOADED_AT = 0

def create_session():
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    adapter = HTTPAdapter(max_retries=retries, pool_maxsize=25)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"User-Agent": "xadrezjovemes-ranking/1.0"})
    return s

def safe_int(v):
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return 0

def get_team_members(session):
    url = TEAM_URL.format(TEAM_ID)
    resp = session.get(url, stream=True, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    users = []
    seen = set()
    for line in resp.iter_lines():
        if not line:
            continue
        try:
            obj = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        username = obj.get("id") or obj.get("username") or (obj.get("user") or {}).get("id")
        if username and username not in seen:
            seen.add(username)
            users.append(username)
    return users

def fetch_user_once(session, username, timeout):
    try:
        resp = session.get(USER_URL.format(username), timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        perfs = data.get("perfs", {}) or {}
        blitz = perfs.get("blitz", {}).get("rating")
        if blitz is None:
            return None
        blitz = safe_int(blitz)
        if blitz <= 0:
            return None
        bullet = safe_int(perfs.get("bullet", {}).get("rating") or 0)
        rapid = safe_int(perfs.get("rapid", {}).get("rating") or 0)
        seen_at = safe_int(data.get("seenAt") or data.get("seen_at") or 0)
        return {
            "username": username,
            "blitz": blitz,
            "bullet": bullet,
            "rapid": rapid,
            "seenAt": seen_at,
            "profile": f"https://lichess.org/@/{quote(username)}",
        }
    except Exception:
        return None

def fetch_user_with_retries(session, username):
    timeout = REQUEST_TIMEOUT
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        res = fetch_user_once(session, username, timeout=timeout)
        if res is not None:
            return res
        time.sleep(RETRY_BACKOFF * attempt)
        timeout = min(timeout * 1.5, REQUEST_TIMEOUT * 2)
    return None

def load_players():
    global PLAYERS, DATA_LOADED_AT
    print("🔍 Carregando lista de membros e ratings (pode demorar) ...")
    with create_session() as session:
        members = get_team_members(session)
        if not members:
            print("Nenhum membro encontrado no feed do time.")
            PLAYERS = []
            return
        print(f"➡️  {len(members)} membros encontrados. Coletando dados individuais (precisão priorizada)...")

        players = []
        failed = []

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(members)))) as exe:
            futures = {exe.submit(fetch_user_with_retries, session, u): u for u in members}
            completed = 0
            for fut in as_completed(futures):
                completed += 1
                res = fut.result()
                if res is None:
                    failed.append(futures[fut])
                else:
                    players.append(res)
                if completed % 20 == 0 or completed == len(members):
                    print(f"Progresso: {completed}/{len(members)} (válidos: {len(players)}, falhas: {len(failed)})")

        if failed:
            print(f"🔁 Re-tentando sequencialmente {len(failed)} falhas...")
            for i, u in enumerate(failed, 1):
                res = None
                for attempt in range(1, RETRY_ATTEMPTS + 2):
                    res = fetch_user_once(session, u, timeout=REQUEST_TIMEOUT * 2)
                    if res is not None:
                        players.append(res)
                        break
                    time.sleep(RETRY_BACKOFF * attempt)
                if i % 20 == 0 or i == len(failed):
                    print(f"Retries: {i}/{len(failed)} (adicionados: {len(players)})")

        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - ACTIVE_DAYS * 24 * 3600 * 1000
        active_players = [p for p in players if p.get("seenAt", 0) >= cutoff_ms]

        PLAYERS = active_players
        DATA_LOADED_AT = int(time.time() * 1000)
        print(f"✅ Dados carregados: {len(PLAYERS)} jogadores ativos encontrados.")