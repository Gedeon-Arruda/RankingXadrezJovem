import requests
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, jsonify, request, render_template_string
from urllib.parse import quote

TEAM_ID = "xadrezjovemes"
TEAM_URL = "https://lichess.org/api/team/{}/users"
USER_URL = "https://lichess.org/api/user/{}"

# Priorizar acurácia: pouca concorrência e mais retries
MAX_WORKERS = 8
ACTIVE_DAYS = 30
REQUEST_TIMEOUT = 8
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.0  # seconds, multiplicativo

app = Flask(__name__)
PLAYERS = []          # lista carregada no startup (dicionários)
DATA_LOADED_AT = 0    # timestamp ms

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
    """Retorna lista única de usernames do time (NDJSON)."""
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
    """Uma tentativa de buscar o usuário e normalizar campos; retorna dict ou None."""
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
    """Tenta várias vezes com backoff; retorna dict ou None."""
    timeout = REQUEST_TIMEOUT
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        res = fetch_user_once(session, username, timeout=timeout)
        if res is not None:
            return res
        time.sleep(RETRY_BACKOFF * attempt)
        timeout = min(timeout * 1.5, REQUEST_TIMEOUT * 2)
    return None

def load_players():
    """Carrega dados dos membros, filtra por atividade e popula PLAYERS global."""
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

        # re-tentar sequencialmente as falhas para maximizar acurácia
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

        # filtrar por atividade nos últimos ACTIVE_DAYS
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - ACTIVE_DAYS * 24 * 3600 * 1000
        active_players = [p for p in players if p.get("seenAt", 0) >= cutoff_ms]

        PLAYERS = active_players
        DATA_LOADED_AT = int(time.time() * 1000)
        print(f"✅ Dados carregados: {len(PLAYERS)} jogadores ativos encontrados.")

@app.route("/api/players")
def api_players():
    """
    Params:
      page (int, default 1)
      per_page (int, default 20)
      sort (str: blitz|bullet|rapid|username, default blitz)
      order (str: desc|asc, default desc)
    """
    if not PLAYERS:
        return jsonify({"error": "dados não carregados ainda"}), 503

    try:
        page = max(1, int(request.args.get("page", 1)))
    except Exception:
        page = 1
    try:
        per_page = max(1, int(request.args.get("per_page", 20)))
    except Exception:
        per_page = 20

    sort = request.args.get("sort", "blitz")
    if sort not in ("blitz", "bullet", "rapid", "username"):
        sort = "blitz"
    order = request.args.get("order", "desc")
    reverse = (order != "asc")

    # sort safely; username uses str lower for consistent ordering
    if sort == "username":
        data = sorted(PLAYERS, key=lambda x: x.get("username", "").lower(), reverse=reverse)
    else:
        data = sorted(PLAYERS, key=lambda x: x.get(sort, 0), reverse=reverse)

    total = len(data)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = data[start:end]

    # Return minimal fields
    resp_items = [
        {
            "username": p["username"],
            "blitz": p["blitz"],
            "bullet": p["bullet"],
            "rapid": p["rapid"],
            "seenAt": p["seenAt"],
            "profile": p["profile"],
        } for p in page_items
    ]

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "sort": sort,
        "order": order,
        "data_loaded_at": DATA_LOADED_AT,
        "items": resp_items,
    })

# Simple admin endpoint to refresh data manually
@app.route("/admin/refresh", methods=["POST"])
def admin_refresh():
    # Segurança simples: só permite local requests
    if request.remote_addr not in ("127.0.0.1", "localhost", "::1"):
        return jsonify({"error": "forbidden"}), 403
    load_players()
    return jsonify({"status": "ok", "loaded": len(PLAYERS)})

# Improved single-file frontend (styled)
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Ranking xadrezjovemes - ativos</title>
  <style>
    :root{
      --bg:#f5f7fb;
      --card:#fff;
      --muted:#6b7280;
      --accent:#0f69ff;
      --accent-2:#0b61d6;
      --success:#16a34a;
      --danger:#ef4444;
      --table-border:#e6e9ee;
    }
    *{box-sizing:border-box}
    body{font-family:Inter,Segoe UI,Roboto,Arial,Helvetica,sans-serif;background:linear-gradient(180deg,var(--bg),#ecf1fb);margin:0;padding:28px;color:#111}
    .wrap{max-width:1100px;margin:0 auto}
    header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
    h1{font-size:1.25rem;margin:0;color:#0b1220}
    .sub{color:var(--muted);font-size:0.95rem}
    .card{background:var(--card);border-radius:12px;box-shadow:0 6px 20px rgba(17,24,39,0.06);padding:18px}
    .controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
    .controls label{font-size:0.9rem;color:var(--muted)}
    select,input[type=number]{padding:8px 10px;border:1px solid #d6dbe8;border-radius:8px;background:#fff}
    button.btn{background:linear-gradient(180deg,var(--accent),var(--accent-2));color:#fff;border:none;padding:8px 12px;border-radius:8px;cursor:pointer}
    button.btn:disabled{opacity:.6;cursor:not-allowed}
    .info{color:var(--muted);margin-left:8px;font-size:0.9rem}
    .table-wrap{overflow:auto;border-radius:8px;border:1px solid var(--table-border);background:linear-gradient(180deg,#fff,#fbfdff)}
    table{width:100%;border-collapse:collapse;min-width:640px}
    thead th{position:sticky;top:0;background:linear-gradient(180deg,#ffffff,#f8fbff);padding:12px 10px;text-align:left;border-bottom:1px solid var(--table-border);font-weight:600;color:#111}
    tbody td{padding:12px 10px;border-bottom:1px solid var(--table-border);vertical-align:middle}
    tbody tr:hover{background:linear-gradient(90deg,rgba(15,105,255,0.03),transparent)}
    .rank{width:56px;font-weight:700;color:var(--accent)}
    .user a{color:#0b1220;text-decoration:none;font-weight:600}
    .user a:hover{text-decoration:underline}
    .rating{font-weight:700}
    .small{font-size:0.85rem;color:var(--muted)}
    .badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#f1f8ff;color:var(--accent);font-weight:600;font-size:0.85rem}
    .pager{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:12px}
    .pager button{padding:6px 10px;border-radius:8px;border:1px solid #e6e9ee;background:#fff;cursor:pointer}
    .pager button[disabled]{opacity:.6;cursor:not-allowed}
    .spinner{width:18px;height:18px;border-radius:50%;border:3px solid rgba(0,0,0,0.08);border-top-color:var(--accent);animation:spin 1s linear infinite;display:inline-block;vertical-align:middle;margin-right:8px}
    @keyframes spin{to{transform:rotate(360deg)}}
    th.sortable{cursor:pointer;user-select:none}
    th.sortable .arrow{margin-left:8px;font-size:0.75rem;color:var(--muted)}
    .seen{color:var(--muted);font-size:0.85rem}
    @media (max-width:720px){
      .controls{flex-direction:column;align-items:flex-start}
      .rank{width:42px}
      thead th, tbody td{padding:10px 8px}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Ranking xadrezjovemes</h1>
        <div class="sub">Jogadores ativos (últimos {{days}} dias)</div>
      </div>
      <div class="badge">Ativos: <span id="totalBadge">—</span></div>
    </header>

    <div class="card">
      <div class="controls">
        <label>Pesquisar:
          <input id="q" type="text" placeholder="buscar usuário..." oninput="debouncedSearch()" />
        </label>

        <label>Por página:
          <select id="perPage" onchange="fetchPage(1)">
            <option value="10">10</option>
            <option value="20" selected>20</option>
            <option value="50">50</option>
          </select>
        </label>

        <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
          <button id="reload" class="btn">Recarregar dados</button>
          <div id="info" class="info">—</div>
        </div>
      </div>

      <div class="table-wrap" id="tableWrap">
        <table>
          <thead>
            <tr>
              <th class="rank">#</th>
              <th class="sortable" data-sort="username">Usuário <span class="arrow">↕</span></th>
              <th class="sortable" data-sort="blitz">Blitz <span class="arrow">↕</span></th>
              <th class="sortable" data-sort="bullet">Bullet <span class="arrow">↕</span></th>
              <th class="sortable" data-sort="rapid">Rapid <span class="arrow">↕</span></th>
              <th>Último login</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>

      <div class="pager" id="pager"></div>
    </div>

    <footer style="margin-top:12px;color:var(--muted);font-size:0.9rem">
      Dados fornecidos pelo Lichess — página local. Clique no nome para abrir perfil.
    </footer>
  </div>

<script>
let currentPage = 1;
let currentSort = 'blitz';
let currentOrder = 'desc';
let currentQuery = '';
let debounceTimer = null;

document.getElementById('reload').addEventListener('click', () => {
  document.getElementById('reload').disabled = true;
  document.getElementById('info').innerHTML = '<span class="spinner"></span> Recarregando...';
  fetch('/admin/refresh', {method:'POST'})
    .then(r => r.json())
    .then(js => {
      document.getElementById('reload').disabled = false;
      fetchPage(1, true);
    })
    .catch(e => {
      console.error(e);
      document.getElementById('reload').disabled = false;
      document.getElementById('info').textContent = 'Erro ao recarregar';
    });
});

// headers clickable sorting
document.querySelectorAll('th.sortable').forEach(th => {
  th.addEventListener('click', () => {
    const s = th.getAttribute('data-sort');
    if (currentSort === s) {
      currentOrder = (currentOrder === 'desc') ? 'asc' : 'desc';
    } else {
      currentSort = s;
      currentOrder = (s === 'username') ? 'asc' : 'desc';
    }
    // visual arrow update
    document.querySelectorAll('th.sortable .arrow').forEach(a => a.textContent = '↕');
    th.querySelector('.arrow').textContent = (currentOrder === 'desc') ? '↓' : '↑';
    fetchPage(1);
  });
});

function debouncedSearch(){
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    currentQuery = document.getElementById('q').value.trim().toLowerCase();
    fetchPage(1);
  }, 350);
}

function fetchPage(page=1, forceRefresh=false){
  currentPage = page;
  const perPage = parseInt(document.getElementById('perPage').value,10) || 20;
  const q = encodeURIComponent(currentQuery || '');
  document.getElementById('info').innerHTML = '<span class="spinner"></span> Carregando...';
  fetch(`/api/players?page=${page}&per_page=${perPage}&sort=${currentSort}&order=${currentOrder}`)
    .then(r => r.json())
    .then(js => {
      if(js.error){ document.getElementById('info').textContent = js.error; return; }
      document.getElementById('totalBadge').textContent = js.total;
      document.getElementById('info').textContent = `Total: ${js.total} — carregado em ${new Date(js.data_loaded_at).toLocaleString()}`;

      // apply client-side search filter (fast, since page size is small)
      let items = js.items || [];
      if(currentQuery){
        items = items.filter(p => p.username.toLowerCase().includes(currentQuery));
      }

      const tbody = document.getElementById('tbody');
      tbody.innerHTML = '';
      items.forEach((p, idx) => {
        const tr = document.createElement('tr');
        const rank = (page-1)*perPage + idx + 1;
        tr.innerHTML = `
          <td class="rank">${rank}</td>
          <td class="user"><a href="${p.profile}" target="_blank" rel="noopener">${escapeHtml(p.username)}</a><div class="small">${p.username}</div></td>
          <td class="rating">${p.blitz}</td>
          <td class="rating">${p.bullet}</td>
          <td class="rating">${p.rapid}</td>
          <td class="seen">${formatSeen(p.seenAt)}</td>
        `;
        tbody.appendChild(tr);
      });
      renderPager(js.total, page, perPage);
    })
    .catch(e => {
      console.error(e);
      document.getElementById('info').textContent = 'Erro ao carregar';
    });
}

function renderPager(total, page, perPage){
  const pager = document.getElementById('pager');
  pager.innerHTML = '';
  const totalPages = Math.max(1, Math.ceil(total / perPage));
  const createBtn = (text, p) => {
    const b = document.createElement('button');
    b.textContent = text;
    b.disabled = (p === page);
    b.onclick = () => fetchPage(p);
    return b;
  };
  pager.appendChild(createBtn('« Primeiro', 1));
  pager.appendChild(createBtn('‹ Prev', Math.max(1, page-1)));
  const start = Math.max(1, page-2);
  const end = Math.min(totalPages, page+2);
  for(let p=start;p<=end;p++){
    pager.appendChild(createBtn(p, p));
  }
  pager.appendChild(createBtn('Next ›', Math.min(totalPages, page+1)));
  pager.appendChild(createBtn('Último »', totalPages));
}

function formatSeen(ms){
  if(!ms || ms <= 0) return 'N/A';
  const diff = Date.now() - ms;
  const days = Math.floor(diff / (24*3600*1000));
  if(days === 0) return 'Hoje';
  if(days === 1) return '1 dia';
  if(days < 30) return `${days} dias`;
  const months = Math.floor(days / 30);
  if(months < 12) return `${months} meses`;
  const years = Math.floor(months / 12);
  return `${years} anos`;
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, function(m){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]});
}

// init
window.addEventListener('load', () => {
  // set default arrows
  document.querySelectorAll('th.sortable .arrow').forEach(a => a.textContent = '↕');
  // highlight default sort
  const el = document.querySelector(`th.sortable[data-sort="${currentSort}"]`);
  if(el) el.querySelector('.arrow').textContent = '↓';
  fetchPage(1);
});
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX_HTML, days=ACTIVE_DAYS)

if __name__ == "__main__":
    # carrega dados uma vez ao iniciar (sincronicamente)
    load_players()
    # inicia servidor local
    print("Abra http://127.0.0.1:8000 no navegador")
    app.run(host="127.0.0.1", port=8000, debug=False)
# filepath: c:\Users\Gedeon\xadrezjovemes-ranking\ranking.py
import requests
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, jsonify, request, render_template_string
from urllib.parse import quote

TEAM_ID = "xadrezjovemes"
TEAM_URL = "https://lichess.org/api/team/{}/users"
USER_URL = "https://lichess.org/api/user/{}"

# Priorizar acurácia: pouca concorrência e mais retries
MAX_WORKERS = 8
ACTIVE_DAYS = 30
REQUEST_TIMEOUT = 8
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.0  # seconds, multiplicativo

app = Flask(__name__)
PLAYERS = []          # lista carregada no startup (dicionários)
DATA_LOADED_AT = 0    # timestamp ms

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
    """Retorna lista única de usernames do time (NDJSON)."""
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
    """Uma tentativa de buscar o usuário e normalizar campos; retorna dict ou None."""
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
    """Tenta várias vezes com backoff; retorna dict ou None."""
    timeout = REQUEST_TIMEOUT
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        res = fetch_user_once(session, username, timeout=timeout)
        if res is not None:
            return res
        time.sleep(RETRY_BACKOFF * attempt)
        timeout = min(timeout * 1.5, REQUEST_TIMEOUT * 2)
    return None

def load_players():
    """Carrega dados dos membros, filtra por atividade e popula PLAYERS global."""
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

        # re-tentar sequencialmente as falhas para maximizar acurácia
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

        # filtrar por atividade nos últimos ACTIVE_DAYS
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - ACTIVE_DAYS * 24 * 3600 * 1000
        active_players = [p for p in players if p.get("seenAt", 0) >= cutoff_ms]

        PLAYERS = active_players
        DATA_LOADED_AT = int(time.time() * 1000)
        print(f"✅ Dados carregados: {len(PLAYERS)} jogadores ativos encontrados.")

@app.route("/api/players")
def api_players():
    """
    Params:
      page (int, default 1)
      per_page (int, default 20)
      sort (str: blitz|bullet|rapid|username, default blitz)
      order (str: desc|asc, default desc)
    """
    if not PLAYERS:
        return jsonify({"error": "dados não carregados ainda"}), 503

    try:
        page = max(1, int(request.args.get("page", 1)))
    except Exception:
        page = 1
    try:
        per_page = max(1, int(request.args.get("per_page", 20)))
    except Exception:
        per_page = 20

    sort = request.args.get("sort", "blitz")
    if sort not in ("blitz", "bullet", "rapid", "username"):
        sort = "blitz"
    order = request.args.get("order", "desc")
    reverse = (order != "asc")

    # sort safely; username uses str lower for consistent ordering
    if sort == "username":
        data = sorted(PLAYERS, key=lambda x: x.get("username", "").lower(), reverse=reverse)
    else:
        data = sorted(PLAYERS, key=lambda x: x.get(sort, 0), reverse=reverse)

    total = len(data)
    start = (page - 1) * per_page
    end = start + per_page
    page_items = data[start:end]

    # Return minimal fields
    resp_items = [
        {
            "username": p["username"],
            "blitz": p["blitz"],
            "bullet": p["bullet"],
            "rapid": p["rapid"],
            "seenAt": p["seenAt"],
            "profile": p["profile"],
        } for p in page_items
    ]

    return jsonify({
        "total": total,
        "page": page,
        "per_page": per_page,
        "sort": sort,
        "order": order,
        "data_loaded_at": DATA_LOADED_AT,
        "items": resp_items,
    })

# Simple admin endpoint to refresh data manually
@app.route("/admin/refresh", methods=["POST"])
def admin_refresh():
    # Segurança simples: só permite local requests
    if request.remote_addr not in ("127.0.0.1", "localhost", "::1"):
        return jsonify({"error": "forbidden"}), 403
    load_players()
    return jsonify({"status": "ok", "loaded": len(PLAYERS)})

# Improved single-file frontend (styled)
INDEX_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Ranking xadrezjovemes - ativos</title>
  <style>
    :root{
      --bg:#f5f7fb;
      --card:#fff;
      --muted:#6b7280;
      --accent:#0f69ff;
      --accent-2:#0b61d6;
      --success:#16a34a;
      --danger:#ef4444;
      --table-border:#e6e9ee;
    }
    *{box-sizing:border-box}
    body{font-family:Inter,Segoe UI,Roboto,Arial,Helvetica,sans-serif;background:linear-gradient(180deg,var(--bg),#ecf1fb);margin:0;padding:28px;color:#111}
    .wrap{max-width:1100px;margin:0 auto}
    header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
    h1{font-size:1.25rem;margin:0;color:#0b1220}
    .sub{color:var(--muted);font-size:0.95rem}
    .card{background:var(--card);border-radius:12px;box-shadow:0 6px 20px rgba(17,24,39,0.06);padding:18px}
    .controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
    .controls label{font-size:0.9rem;color:var(--muted)}
    select,input[type=number]{padding:8px 10px;border:1px solid #d6dbe8;border-radius:8px;background:#fff}
    button.btn{background:linear-gradient(180deg,var(--accent),var(--accent-2));color:#fff;border:none;padding:8px 12px;border-radius:8px;cursor:pointer}
    button.btn:disabled{opacity:.6;cursor:not-allowed}
    .info{color:var(--muted);margin-left:8px;font-size:0.9rem}
    .table-wrap{overflow:auto;border-radius:8px;border:1px solid var(--table-border);background:linear-gradient(180deg,#fff,#fbfdff)}
    table{width:100%;border-collapse:collapse;min-width:640px}
    thead th{position:sticky;top:0;background:linear-gradient(180deg,#ffffff,#f8fbff);padding:12px 10px;text-align:left;border-bottom:1px solid var(--table-border);font-weight:600;color:#111}
    tbody td{padding:12px 10px;border-bottom:1px solid var(--table-border);vertical-align:middle}
    tbody tr:hover{background:linear-gradient(90deg,rgba(15,105,255,0.03),transparent)}
    .rank{width:56px;font-weight:700;color:var(--accent)}
    .user a{color:#0b1220;text-decoration:none;font-weight:600}
    .user a:hover{text-decoration:underline}
    .rating{font-weight:700}
    .small{font-size:0.85rem;color:var(--muted)}
    .badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#f1f8ff;color:var(--accent);font-weight:600;font-size:0.85rem}
    .pager{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:12px}
    .pager button{padding:6px 10px;border-radius:8px;border:1px solid #e6e9ee;background:#fff;cursor:pointer}
    .pager button[disabled]{opacity:.6;cursor:not-allowed}
    .spinner{width:18px;height:18px;border-radius:50%;border:3px solid rgba(0,0,0,0.08);border-top-color:var(--accent);animation:spin 1s linear infinite;display:inline-block;vertical-align:middle;margin-right:8px}
    @keyframes spin{to{transform:rotate(360deg)}}
    th.sortable{cursor:pointer;user-select:none}
    th.sortable .arrow{margin-left:8px;font-size:0.75rem;color:var(--muted)}
    .seen{color:var(--muted);font-size:0.85rem}
    @media (max-width:720px){
      .controls{flex-direction:column;align-items:flex-start}
      .rank{width:42px}
      thead th, tbody td{padding:10px 8px}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1>Ranking xadrezjovemes</h1>
        <div class="sub">Jogadores ativos (últimos {{days}} dias)</div>
      </div>
      <div class="badge">Ativos: <span id="totalBadge">—</span></div>
    </header>

    <div class="card">
      <div class="controls">
        <label>Pesquisar:
          <input id="q" type="text" placeholder="buscar usuário..." oninput="debouncedSearch()" />
        </label>

        <label>Por página:
          <select id="perPage" onchange="fetchPage(1)">
            <option value="10">10</option>
            <option value="20" selected>20</option>
            <option value="50">50</option>
          </select>
        </label>

        <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
          <button id="reload" class="btn">Recarregar dados</button>
          <div id="info" class="info">—</div>
        </div>
      </div>

      <div class="table-wrap" id="tableWrap">
        <table>
          <thead>
            <tr>
              <th class="rank">#</th>
              <th class="sortable" data-sort="username">Usuário <span class="arrow">↕</span></th>
              <th class="sortable" data-sort="blitz">Blitz <span class="arrow">↕</span></th>
              <th class="sortable" data-sort="bullet">Bullet <span class="arrow">↕</span></th>
              <th class="sortable" data-sort="rapid">Rapid <span class="arrow">↕</span></th>
              <th>Último login</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>

      <div class="pager" id="pager"></div>
    </div>

    <footer style="margin-top:12px;color:var(--muted);font-size:0.9rem">
      Dados fornecidos pelo Lichess — página local. Clique no nome para abrir perfil.
    </footer>
  </div>

<script>
let currentPage = 1;
let currentSort = 'blitz';
let currentOrder = 'desc';
let currentQuery = '';
let debounceTimer = null;

document.getElementById('reload').addEventListener('click', () => {
  document.getElementById('reload').disabled = true;
  document.getElementById('info').innerHTML = '<span class="spinner"></span> Recarregando...';
  fetch('/admin/refresh', {method:'POST'})
    .then(r => r.json())
    .then(js => {
      document.getElementById('reload').disabled = false;
      fetchPage(1, true);
    })
    .catch(e => {
      console.error(e);
      document.getElementById('reload').disabled = false;
      document.getElementById('info').textContent = 'Erro ao recarregar';
    });
});

// headers clickable sorting
document.querySelectorAll('th.sortable').forEach(th => {
  th.addEventListener('click', () => {
    const s = th.getAttribute('data-sort');
    if (currentSort === s) {
      currentOrder = (currentOrder === 'desc') ? 'asc' : 'desc';
    } else {
      currentSort = s;
      currentOrder = (s === 'username') ? 'asc' : 'desc';
    }
    // visual arrow update
    document.querySelectorAll('th.sortable .arrow').forEach(a => a.textContent = '↕');
    th.querySelector('.arrow').textContent = (currentOrder === 'desc') ? '↓' : '↑';
    fetchPage(1);
  });
});

function debouncedSearch(){
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    currentQuery = document.getElementById('q').value.trim().toLowerCase();
    fetchPage(1);
  }, 350);
}

function fetchPage(page=1, forceRefresh=false){
  currentPage = page;
  const perPage = parseInt(document.getElementById('perPage').value,10) || 20;
  const q = encodeURIComponent(currentQuery || '');
  document.getElementById('info').innerHTML = '<span class="spinner"></span> Carregando...';
  fetch(`/api/players?page=${page}&per_page=${perPage}&sort=${currentSort}&order=${currentOrder}`)
    .then(r => r.json())
    .then(js => {
      if(js.error){ document.getElementById('info').textContent = js.error; return; }
      document.getElementById('totalBadge').textContent = js.total;
      document.getElementById('info').textContent = `Total: ${js.total} — carregado em ${new Date(js.data_loaded_at).toLocaleString()}`;

      // apply client-side search filter (fast, since page size is small)
      let items = js.items || [];
      if(currentQuery){
        items = items.filter(p => p.username.toLowerCase().includes(currentQuery));
      }

      const tbody = document.getElementById('tbody');
      tbody.innerHTML = '';
      items.forEach((p, idx) => {
        const tr = document.createElement('tr');
        const rank = (page-1)*perPage + idx + 1;
        tr.innerHTML = `
          <td class="rank">${rank}</td>
          <td class="user"><a href="${p.profile}" target="_blank" rel="noopener">${escapeHtml(p.username)}</a><div class="small">${p.username}</div></td>
          <td class="rating">${p.blitz}</td>
          <td class="rating">${p.bullet}</td>
          <td class="rating">${p.rapid}</td>
          <td class="seen">${formatSeen(p.seenAt)}</td>
        `;
        tbody.appendChild(tr);
      });
      renderPager(js.total, page, perPage);
    })
    .catch(e => {
      console.error(e);
      document.getElementById('info').textContent = 'Erro ao carregar';
    });
}

function renderPager(total, page, perPage){
  const pager = document.getElementById('pager');
  pager.innerHTML = '';
  const totalPages = Math.max(1, Math.ceil(total / perPage));
  const createBtn = (text, p) => {
    const b = document.createElement('button');
    b.textContent = text;
    b.disabled = (p === page);
    b.onclick = () => fetchPage(p);
    return b;
  };
  pager.appendChild(createBtn('« Primeiro', 1));
  pager.appendChild(createBtn('‹ Prev', Math.max(1, page-1)));
  const start = Math.max(1, page-2);
  const end = Math.min(totalPages, page+2);
  for(let p=start;p<=end;p++){
    pager.appendChild(createBtn(p, p));
  }
  pager.appendChild(createBtn('Next ›', Math.min(totalPages, page+1)));
  pager.appendChild(createBtn('Último »', totalPages));
}

function formatSeen(ms){
  if(!ms || ms <= 0) return 'N/A';
  const diff = Date.now() - ms;
  const days = Math.floor(diff / (24*3600*1000));
  if(days === 0) return 'Hoje';
  if(days === 1) return '1 dia';
  if(days < 30) return `${days} dias`;
  const months = Math.floor(days / 30);
  if(months < 12) return `${months} meses`;
  const years = Math.floor(months / 12);
  return `${years} anos`;
}

function escapeHtml(s){
  return String(s).replace(/[&<>"']/g, function(m){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]});
}

// init
window.addEventListener('load', () => {
  // set default arrows
  document.querySelectorAll('th.sortable .arrow').forEach(a => a.textContent = '↕');
  // highlight default sort
  const el = document.querySelector(`th.sortable[data-sort="${currentSort}"]`);
  if(el) el.querySelector('.arrow').textContent = '↓';
  fetchPage(1);
});
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX_HTML, days=ACTIVE_DAYS)

if __name__ == "__main__":
    # carrega dados uma vez ao iniciar (sincronicamente)
    load_players()
    # inicia servidor local
    print("Abra http://127.0.0.1:8000 no navegador")
    app.run(host="127.0.0.1", port=8000, debug=False)