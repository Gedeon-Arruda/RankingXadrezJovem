#!/usr/bin/env python3
# coding: utf-8

import requests
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, jsonify, request, render_template_string, make_response
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

        # Captura nome real do profile; usa fallback pedido pelo usuário
        profile_data = data.get("profile") or {}
        real_name = profile_data.get("realName") or profile_data.get("name") or profile_data.get("fullName") or "Nome não encontrato"

        return {
            "username": username,
            "name": real_name,
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
                try:
                    res = fut.result()
                except Exception:
                    res = None
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

    # Return minimal fields (agora incluindo 'name')
    resp_items = [
        {
            "username": p["username"],
            "name": p.get("name", "Nome não encontrato"),
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

# rota compatível com o frontend estático: players.json
@app.route("/players.json")
def players_json():
    # devolve full list (sem paginação) no formato que o frontend já esperava
    payload = {
        "players": [
            {
                "username": p["username"],
                "name": p.get("name", "Nome não encontrato"),
                "blitz": p["blitz"],
                "bullet": p["bullet"],
                "rapid": p["rapid"],
                "seenAt": p["seenAt"],
                "profile": p["profile"],
            } for p in PLAYERS
        ],
        "generated_at": DATA_LOADED_AT
    }
    resp = make_response(json.dumps(payload), 200)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    return resp

# Simple admin endpoint to refresh data manually
@app.route("/admin/refresh", methods=["POST"])
def admin_refresh():
    # Segurança simples: só permite local requests
    if request.remote_addr not in ("127.0.0.1", "localhost", "::1"):
        return jsonify({"error": "forbidden"}), 403
    load_players()
    return jsonify({"status": "ok", "loaded": len(PLAYERS)})

# FULL frontend HTML (INDEX_HTML) - o JS carrega ./players.json e exibe `name`
INDEX_HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Ranking Xadrez Jovem ES</title>
  <link rel="icon" href="data:;base64,iVBORw0KGgo=">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg:#f3f6fb;
      --card:#ffffff;
      --muted:#6b7280;
      --accent:#0066ff;
      --accent-2:#004ecb;
      --table-border:#e6e9ee;
      --glass: rgba(255,255,255,0.7);
    }
    *{box-sizing:border-box}
    html,body{height:100%}
    body{
      margin:0;
      font-family:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
      background:linear-gradient(180deg,var(--bg),#eef4ff);
      color:#0b1220;
      -webkit-font-smoothing:antialiased;
      -moz-osx-font-smoothing:grayscale;
      padding:28px;
    }
    .wrap{max-width:1200px;margin:0 auto}
    header.site{
      display:flex;align-items:center;gap:16px;justify-content:space-between;margin-bottom:20px;
    }
    .brand{
      display:flex;gap:12px;align-items:center;
    }
    .logo{
      width:56px;height:56px;border-radius:10px;background:linear-gradient(135deg,var(--accent),var(--accent-2));
      display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:18px;box-shadow:0 6px 18px rgba(6,34,102,0.12);
    }
    h1{font-size:1.35rem;margin:0}
    .subtitle{color:var(--muted);font-size:0.95rem;margin-top:2px}
    .top-meta{display:flex;gap:10px;align-items:center}
    .badge{
      background:linear-gradient(180deg,#f1f9ff,#eef6ff);color:var(--accent);padding:6px 10px;border-radius:999px;border:1px solid rgba(11,102,255,0.08);font-weight:700;font-size:0.9rem;
    }

    .card{
      background:var(--card);
      border-radius:14px;
      padding:16px;
      box-shadow:0 10px 30px rgba(12,32,80,0.06);
    }

    .controls{
      display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px;
    }
    .controls .left{display:flex;gap:8px;align-items:center}
    .search{
      display:flex;align-items:center;gap:8px;background:var(--glass);padding:8px;border-radius:10px;border:1px solid rgba(11,20,40,0.04);
    }
    .search input{
      border:0;background:transparent;outline:none;padding:6px 8px;font-size:0.95rem;width:240px;
    }
    select,button{
      padding:8px 10px;border-radius:8px;border:1px solid #e6e9ee;background:#fff;font-size:0.95rem;
    }
    button.btn{
      background:linear-gradient(180deg,var(--accent),var(--accent-2));color:#fff;border:none;cursor:pointer;
    }
    .info{color:var(--muted);font-size:0.9rem;margin-left:6px}

    .table-wrap{overflow:auto;border-radius:10px;border:1px solid var(--table-border);background:linear-gradient(180deg,#fff,#fbfdff);margin-top:8px}
    table{width:100%;border-collapse:collapse;min-width:720px}
    thead th{
      position:sticky;top:0;background:linear-gradient(180deg,#ffffff,#f7fbff);padding:12px 14px;text-align:left;border-bottom:1px solid var(--table-border);font-weight:700;font-size:0.95rem;color:#0b1220;
    }
    tbody td{padding:12px 14px;border-bottom:1px solid var(--table-border);vertical-align:middle;font-size:0.95rem;color:#122036}
    tbody tr:hover{background:linear-gradient(90deg,rgba(0,102,255,0.03),transparent)}
    .rank{width:56px;font-weight:700;color:var(--accent)}
    .user a{color:var(--accent);text-decoration:none;font-weight:600}
    .user a:hover{text-decoration:underline}
    .small{display:block;color:var(--muted);font-size:0.82rem;margin-top:4px}
    .rating{font-weight:700;color:#111}
    .seen{color:var(--muted);font-size:0.9rem}

    .pager{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:12px;justify-content:flex-end}
    .pager button{background:#fff;border:1px solid #eef3fb;padding:6px 10px;border-radius:8px;cursor:pointer}
    .pager button[disabled]{opacity:.6;cursor:default}

    @media (max-width:900px){
      .brand h1{font-size:1.05rem}
      .search input{width:140px}
      table{min-width:640px}
    }
    @media (max-width:640px){
      body{padding:14px}
      .controls{flex-direction:column;align-items:flex-start}
      .top-meta{display:none}
      .logo{width:48px;height:48px;font-size:16px}
    }
  </style>
</head>
<body>
  <div class="wrap">
    <header class="site">
      <div class="brand">
        <div class="logo">RJ</div>
        <div>
          <h1>Ranking Xadrez Jovem ES</h1>
          <div class="subtitle">Jogadores ativos — últimos 30 dias</div>
        </div>
      </div>
      <div class="top-meta">
        <div class="badge">Ativos: <strong id="totalBadge">—</strong></div>
      </div>
    </header>

    <div class="card">
      <div class="controls">
        <div class="left">
          <div class="search" title="Pesquisar usuário">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" style="opacity:.7"><path d="M21 21l-4.35-4.35" stroke="#456" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="11" cy="11" r="6" stroke="#456" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            <input id="q" type="search" placeholder="buscar usuário..." />
          </div>

          <label>
            <select id="sort">
              <option value="blitz">Blitz</option>
              <option value="bullet">Bullet</option>
              <option value="rapid">Rapid</option>
              <option value="username">Usuário</option>
            </select>
          </label>

          <label>
            <select id="order">
              <option value="desc">Desc</option>
              <option value="asc">Asc</option>
            </select>
          </label>

          <label>
            <select id="perPage">
              <option value="10">10</option>
              <option value="20" selected>20</option>
              <option value="50">50</option>
            </select>
          </label>
        </div>

        <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
          <button id="reload" class="btn">Recarregar</button>
          <div id="info" class="info">—</div>
        </div>
      </div>

      <div class="table-wrap" id="tableWrap">
        <table>
          <thead>
            <tr>
              <th class="rank">#</th>
              <th>Usuário</th>
              <th>Blitz</th>
              <th>Bullet</th>
              <th>Rapid</th>
              <th>Último login</th>
            </tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>

      <div class="pager" id="pager"></div>
    </div>

    <footer style="margin-top:14px;color:var(--muted);font-size:0.9rem;text-align:center">
      Dados: Lichess · Site estático gerado a partir de players.json
    </footer>
  </div>

<script>
/* client-side: load ./players.json, search, sort, paginate */
let all = [];
let filtered = [];
let page = 1;

const qEl = document.getElementById('q');
const sortEl = document.getElementById('sort');
const orderEl = document.getElementById('order');
const perPageEl = document.getElementById('perPage');
const infoEl = document.getElementById('info');
const totalBadge = document.getElementById('totalBadge');

document.getElementById('reload').addEventListener('click', () => {
  loadData(true);
});

qEl.addEventListener('input', ()=>{ page = 1; applyFilters(); });
sortEl.addEventListener('change', ()=>{ page = 1; applyFilters(); });
orderEl.addEventListener('change', ()=>{ page = 1; applyFilters(); });
perPageEl.addEventListener('change', ()=>{ page = 1; renderPage(); });

async function loadData(force=false){
  infoEl.textContent = 'Carregando...';
  try{
    const r = await fetch('./players.json', {cache: force ? 'no-store' : 'default'});
    if(!r.ok) throw new Error('Erro ao buscar players.json');
    const js = await r.json();
    // suporta ambos: { players: [...] } ou { items: [...] }
    all = js.players || js.items || [];
    totalBadge.textContent = all.length;
    const dt = js.generated_at ? new Date(js.generated_at).toLocaleString() : (js.data_loaded_at ? new Date(js.data_loaded_at).toLocaleString() : '—');
    infoEl.textContent = `Total: ${all.length} — gerado: ${dt}`;
    page = 1;
    applyFilters();
  }catch(err){
    console.error(err);
    infoEl.textContent = 'Erro ao carregar';
  }
}

function applyFilters(){
  const q = qEl.value.trim().toLowerCase();
  filtered = all.filter(p => {
    if(!q) return true;
    const uname = (p.username || '').toLowerCase();
    const name = (p.name || '').toLowerCase();
    return uname.includes(q) || name.includes(q);
  });
  const sortKey = sortEl.value;
  const order = orderEl.value === 'asc' ? 1 : -1;
  filtered.sort((a,b)=>{
    let va = sortKey === 'username' ? (a.username || '').toLowerCase() : (a[sortKey] || 0);
    let vb = sortKey === 'username' ? (b.username || '').toLowerCase() : (b[sortKey] || 0);
    if(va < vb) return -1 * order;
    if(va > vb) return 1 * order;
    return 0;
  });
  renderPage();
}

function renderPage(){
  const per = parseInt(perPageEl.value,10) || 20;
  const start = (page-1)*per;
  const pageItems = filtered.slice(start, start+per);
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = '';
  pageItems.forEach((p, idx) => {
    const tr = document.createElement('tr');
    const rank = start + idx + 1;
    // Mostrar username como link e name abaixo (com fallback pedido)
    const displayName = p.name || 'Nome não encontrato';
    tr.innerHTML = `
      <td class="rank">${rank}</td>
      <td class="user">
        <a href="${p.profile}" target="_blank" rel="noopener">${escapeHtml(p.username)}</a>
        <span class="small">${escapeHtml(displayName)}</span>
      </td>
      <td class="rating">${p.blitz}</td>
      <td class="rating">${p.bullet}</td>
      <td class="rating">${p.rapid}</td>
      <td class="seen">${formatSeen(p.seenAt)}</td>
    `;
    tbody.appendChild(tr);
  });
  renderPager(per);
}

function renderPager(per){
  const pager = document.getElementById('pager');
  pager.innerHTML = '';
  const totalPages = Math.max(1, Math.ceil(filtered.length / per));
  const createBtn = (text, p) => {
    const b = document.createElement('button');
    b.textContent = text;
    b.disabled = p === page;
    b.onclick = () => { page = p; renderPage(); };
    return b;
  };
  pager.appendChild(createBtn('«', 1));
  pager.appendChild(createBtn('‹', Math.max(1, page-1)));
  const start = Math.max(1, page-2);
  const end = Math.min(totalPages, page+2);
  for(let i=start;i<=end;i++) pager.appendChild(createBtn(i, i));
  pager.appendChild(createBtn('›', Math.min(totalPages, page+1)));
  pager.appendChild(createBtn('»', totalPages));
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

function escapeHtml(s){ return String(s).replace(/[&<>"']/g, m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }

window.addEventListener('load', loadData);
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
