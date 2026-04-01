import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ----------------------------- Config -----------------------------
MAX_WORKERS = 8
REQUEST_TIMEOUT = 12
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.0
ACTIVE_DAYS = 30

OUT_DIR = "docs"
LICHESS_OUT_FILE = f"{OUT_DIR}/players.json"
CHESSCOM_OUT_FILE = f"{OUT_DIR}/players_chesscom.json"

# Lichess
LICHESS_TEAM_ID = "xadrezjovemes"
LICHESS_TEAM_URL = "https://lichess.org/api/team/{}/users"
LICHESS_USER_URL = "https://lichess.org/api/user/{}"
LICHESS_USER_RATING_HISTORY_URL = "https://lichess.org/api/user/{}/rating-history"

# Chess.com
CHESSCOM_CLUB_ID = "xadrez-jovem-es"
CHESSCOM_CLUB_MEMBERS_URL = "https://api.chess.com/pub/club/{}/members"
CHESSCOM_PLAYER_URL = "https://api.chess.com/pub/player/{}"
CHESSCOM_PLAYER_STATS_URL = "https://api.chess.com/pub/player/{}/stats"


def make_session():
    session = requests.Session()
    retries = Retry(
        total=RETRY_ATTEMPTS,
        backoff_factor=RETRY_BACKOFF,
        status_forcelist=(429, 500, 502, 503, 504),
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update({"User-Agent": "ranking-xadrez-jovem-es-generator/1.1"})
    session.verify = certifi.where()
    return session


def safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def safe_timestamp_ms(value):
    if value is None or value == "":
        return None
    try:
        ts = int(float(value))
    except Exception:
        return None
    # Chess.com usa segundos. Lichess já vem em ms.
    return ts * 1000 if ts < 1_000_000_000_000 else ts


def active_since_days(player, days=ACTIVE_DAYS):
    seen = safe_timestamp_ms(player.get("seenAt"))
    if not seen:
        return False
    age_days = (time.time() * 1000 - seen) / (24 * 3600 * 1000)
    return age_days <= days


def rating_status(diff):
    if diff is None:
        return None
    d = safe_int(diff, default=0)
    if d > 0:
        return "subiu"
    if d < 0:
        return "caiu"
    return "manteve"


def dedupe_players(players):
    by_user = {}

    def score(p):
        return safe_int(p.get("blitz")) + safe_int(p.get("bullet")) + safe_int(p.get("rapid"))

    for player in players:
        username = (player.get("username") or "").strip().lower()
        if not username:
            continue
        previous = by_user.get(username)
        if previous is None:
            by_user[username] = player
            continue
        previous_seen = safe_timestamp_ms(previous.get("seenAt")) or 0
        current_seen = safe_timestamp_ms(player.get("seenAt")) or 0
        if score(player) > score(previous) or current_seen > previous_seen:
            by_user[username] = player

    return list(by_user.values())


def read_previous_map(output_path, write_file):
    prev_players = []
    if not write_file or not os.path.exists(output_path):
        return {}, {}
    try:
        with open(output_path, "r", encoding="utf-8") as f:
            prev = json.load(f)
        prev_players = prev.get("players") or []
    except Exception:
        prev_players = []

    prev_map = {}
    prev_rank_map = {}
    for idx, player in enumerate(prev_players):
        username = (player.get("username") or "").strip().lower()
        if not username:
            continue
        prev_map[username] = player
        prev_rank_map[username] = idx + 1
    return prev_map, prev_rank_map


def enrich_with_deltas_and_positions(players, output_path, write_file):
    prev_map, prev_rank_map = read_previous_map(output_path, write_file)

    for player in players:
        if not (player.get("name") or "").strip():
            player["name"] = "Sem nome registrado"

        username = (player.get("username") or "").strip().lower()
        previous = prev_map.get(username)

        if previous:
            player["blitz_diff"] = safe_int(player.get("blitz")) - safe_int(previous.get("blitz"))
            player["bullet_diff"] = safe_int(player.get("bullet")) - safe_int(previous.get("bullet"))
            player["rapid_diff"] = safe_int(player.get("rapid")) - safe_int(previous.get("rapid"))
        else:
            player["blitz_diff"] = safe_int(player.get("recent_blitz_diff"))
            player["bullet_diff"] = safe_int(player.get("recent_bullet_diff"))
            player["rapid_diff"] = safe_int(player.get("recent_rapid_diff"))

        player.pop("recent_blitz_diff", None)
        player.pop("recent_bullet_diff", None)
        player.pop("recent_rapid_diff", None)

    for idx, player in enumerate(players):
        current_pos = idx + 1
        player["position"] = current_pos

        username = (player.get("username") or "").strip().lower()
        prev_pos = prev_rank_map.get(username)
        if prev_pos is None:
            player["position_change"] = None
            player["position_arrow"] = None
        else:
            change = prev_pos - current_pos
            player["position_change"] = change
            if change > 0:
                player["position_arrow"] = "▲"
            elif change < 0:
                player["position_arrow"] = "▼"
            else:
                player["position_arrow"] = "→"

        player["blitz_status"] = rating_status(player.get("blitz_diff"))
        player["bullet_status"] = rating_status(player.get("bullet_diff"))
        player["rapid_status"] = rating_status(player.get("rapid_diff"))


def write_output(players, output_path, write_file=True):
    payload = {
        "generated_at": int(time.time() * 1000),
        "count": len(players),
        "players": players,
    }

    if write_file:
        out_dir = os.path.dirname(output_path) or "."
        os.makedirs(out_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"Wrote {output_path} ({len(players)} players)")

    return payload


def finalize_players(players, output_path, write_file=True):
    deduped = dedupe_players(players)
    active = [p for p in deduped if active_since_days(p)]
    active_sorted = sorted(
        active,
        key=lambda p: (-safe_int(p.get("blitz")), -safe_int(p.get("bullet")), -safe_int(p.get("rapid"))),
    )
    enrich_with_deltas_and_positions(active_sorted, output_path, write_file)
    return write_output(active_sorted, output_path, write_file)


# ----------------------------- Lichess -----------------------------
def fetch_lichess_team_members(session):
    url = LICHESS_TEAM_URL.format(quote(LICHESS_TEAM_ID))
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    text = response.text.strip()

    users = []
    if not text:
        return users

    try:
        if text.startswith("["):
            for obj in response.json():
                if isinstance(obj, dict):
                    users.append(obj.get("id") or obj.get("username"))
        else:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    users.append(obj.get("id") or obj.get("username") or obj.get("name"))
                except Exception:
                    users.append(line)
    except Exception:
        pass

    return [u for u in users if u]


def extract_lichess_name(profile, user_obj):
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
    if not name and isinstance(user_obj, dict):
        name = user_obj.get("name") or user_obj.get("fullName") or user_obj.get("displayName") or ""
    return (name or "").strip()


def fetch_lichess_rating_history(session, username):
    out = {"blitz": None, "bullet": None, "rapid": None}
    try:
        response = session.get(
            LICHESS_USER_RATING_HISTORY_URL.format(quote(username)),
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            return out
        history = response.json()
        for rec in history:
            name = (rec.get("name") or "").lower()
            if name not in out:
                continue
            points = rec.get("points") or []
            if len(points) >= 2:
                out[name] = safe_int(points[-1][1], default=0) - safe_int(points[-2][1], default=0)
            else:
                out[name] = None
    except Exception:
        return out
    return out


def fetch_lichess_user(session, username):
    try:
        response = session.get(LICHESS_USER_URL.format(quote(username)), timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        user = response.json()

        profile = user.get("profile") or {}
        name = extract_lichess_name(profile, user)
        perfs = user.get("perfs") or {}
        history_diffs = fetch_lichess_rating_history(session, username)

        return {
            "username": username,
            "name": name,
            "profile": profile.get("url") or f"https://lichess.org/@/{username}",
            "blitz": (perfs.get("blitz") or {}).get("rating"),
            "bullet": (perfs.get("bullet") or {}).get("rating"),
            "rapid": (perfs.get("rapid") or {}).get("rating"),
            "seenAt": safe_timestamp_ms(user.get("seenAt") or user.get("lastSeenAt") or user.get("seenAtMillis")),
            "recent_blitz_diff": history_diffs.get("blitz"),
            "recent_bullet_diff": history_diffs.get("bullet"),
            "recent_rapid_diff": history_diffs.get("rapid"),
        }
    except Exception as exc:
        print(f"warning: erro ao buscar Lichess user {username}: {exc}")
        return None


def generate_lichess_json(output_path=LICHESS_OUT_FILE, write_file=True):
    session = make_session()
    print("Lichess: fetching team members...")
    members = fetch_lichess_team_members(session)
    print(f"Lichess members: {len(members)}")

    players = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_lichess_user, session, member): member for member in members}
        for future in as_completed(futures):
            result = future.result()
            if result:
                players.append(result)

    return finalize_players(players, output_path, write_file)


# ----------------------------- Chess.com -----------------------------
def fetch_chesscom_club_members(session):
    response = session.get(CHESSCOM_CLUB_MEMBERS_URL.format(quote(CHESSCOM_CLUB_ID)), timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json() or {}

    usernames = []
    for bucket in ("all_time", "weekly", "monthly"):
        for item in data.get(bucket) or []:
            username = (item.get("username") or "").strip()
            if username:
                usernames.append(username)

    unique = []
    seen = set()
    for username in usernames:
        key = username.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(username)
    return unique


def extract_chesscom_rating(stats_obj, key):
    return ((stats_obj.get(key) or {}).get("last") or {}).get("rating")


def fetch_chesscom_user(session, username):
    try:
        profile_response = session.get(CHESSCOM_PLAYER_URL.format(quote(username)), timeout=REQUEST_TIMEOUT)
        if profile_response.status_code in (404, 410):
            return None
        profile_response.raise_for_status()
        profile = profile_response.json() or {}

        stats_response = session.get(CHESSCOM_PLAYER_STATS_URL.format(quote(username)), timeout=REQUEST_TIMEOUT)
        if stats_response.status_code in (404, 410):
            stats = {}
        else:
            stats_response.raise_for_status()
            stats = stats_response.json() or {}

        return {
            "username": username,
            "name": (profile.get("name") or "").strip(),
            "profile": profile.get("url") or f"https://www.chess.com/member/{username}",
            "blitz": extract_chesscom_rating(stats, "chess_blitz"),
            "bullet": extract_chesscom_rating(stats, "chess_bullet"),
            "rapid": extract_chesscom_rating(stats, "chess_rapid"),
            "seenAt": safe_timestamp_ms(profile.get("last_online")),
        }
    except Exception as exc:
        print(f"warning: erro ao buscar Chess.com user {username}: {exc}")
        return None


def generate_chesscom_json(output_path=CHESSCOM_OUT_FILE, write_file=True):
    session = make_session()
    print("Chess.com: fetching club members...")
    members = fetch_chesscom_club_members(session)
    print(f"Chess.com members: {len(members)}")

    players = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_chesscom_user, session, member): member for member in members}
        for future in as_completed(futures):
            result = future.result()
            if result:
                players.append(result)

    return finalize_players(players, output_path, write_file)


def main():
    parser = argparse.ArgumentParser(
        description="Gera rankings para Lichess e Chess.com no formato consumido pelo site estático."
    )
    parser.add_argument(
        "--source",
        choices=("all", "lichess", "chesscom"),
        default="all",
        help="Fonte a gerar. Default: all",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Caminho de saída (apenas para --source lichess|chesscom).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Imprime JSON no stdout em vez de gravar arquivo(s).",
    )
    args = parser.parse_args()

    write_file = not args.stdout

    if args.source == "all":
        lichess_payload = generate_lichess_json(output_path=LICHESS_OUT_FILE, write_file=write_file)
        chesscom_payload = generate_chesscom_json(output_path=CHESSCOM_OUT_FILE, write_file=write_file)
        if args.stdout:
            print(
                json.dumps(
                    {"lichess": lichess_payload, "chesscom": chesscom_payload},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return

    if args.source == "lichess":
        output = args.output or LICHESS_OUT_FILE
        payload = generate_lichess_json(output_path=output, write_file=write_file)
    else:
        output = args.output or CHESSCOM_OUT_FILE
        payload = generate_chesscom_json(output_path=output, write_file=write_file)

    if args.stdout:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Erro gerando JSON: {exc}")
        raise
