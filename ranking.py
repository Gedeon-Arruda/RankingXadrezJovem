#!/usr/bin/env python3
# coding: utf-8
"""
Flask app que gera o ranking (fetch do team Lichess).
Removido contador de visitas conforme pedido.
"""
import os
import time
import json
import urllib3
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, jsonify, request, render_template_string, make_response, abort
from urllib.parse import quote
import requests

# ---------------------------------------------------------------------
# ATENÇÃO: para contornar erro de SSL no seu ambiente local, este
# script desabilita a verificação de certificados SSL (s.verify = False).
# Isto é inseguro — faça apenas para testes locais. Em produção, use certifi.
# ---------------------------------------------------------------------
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TEAM_ID = "xadrezjovemes"
TEAM_URL = "https://lichess.org/api/team/{}/users"
USER_URL = "https://lichess.org/api/user/{}"

# Priorizar acurácia: pouca concorrência e mais retries
MAX_WORKERS = 8
ACTIVE_DAYS = 30
REQUEST_TIMEOUT = 8
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.0  # seconds, multiplicativo

OUT_DIR = "docs"
OUT_FILE = f"{OUT_DIR}/players.json"

app = Flask(__name__)
PLAYERS = []          # lista carregada no startup (dicionários)
DATA_LOADED_AT = 0    # timestamp ms

# ----------------- seu código de geração / fetch players (adaptado) -----------------
def create_session():
    s = requests.Session()
    # 🚨 DESABILITA VERIFICAÇÃO SSL (apenas para dev local)
    s.verify = False

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

# Simple admin endpoint to refresh data manually
@app.route("/admin/refresh", methods=["POST"])
def admin_refresh():
    # Segurança simples: só permite local requests
    if request.remote_addr not in ("127.0.0.1", "localhost", "::1"):
        return jsonify({"error": "forbidden"}), 403
    load_players()
    return jsonify({"status": "ok", "loaded": len(PLAYERS)})

# ----------------- FULL frontend HTML (INDEX_HTML) - o JS carrega ./players.json e exibe `name` ----------
# Mantive o HTML original que você enviou, mas removi todo o trecho do contador e chamadas a /visit,/stats.
INDEX_HTML = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta http-equiv="cache-control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="expires" content="0">
<meta http-equiv="pragma" content="no-cache">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ranking Xadrez Jovem ES</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
  :root{
    --bg:#f4f6fb;
    --card:#ffffff;
    --muted:#667085;
    --accent:#3b82f6;
    --accent-2:#7c3aed;
    --text:#0b1220;
    --border:rgba(15,23,36,0.06);
    --radius:16px;
    --shadow-lg: 0 20px 50px rgba(16,24,40,0.08);
    --shadow-sm: 0 6px 18px rgba(16,24,40,0.06);
    --glass: rgba(255,255,255,0.7);
    --green:#10b981;
    --red:#ef4444;
    --violet:#6366f1;
    --gray:#9ca3af;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:'Inter',system-ui,-apple-system,Segoe UI,Roboto,"Helvetica Neue",Arial;background:linear-gradient(180deg,#eef2ff 0%,var(--bg) 100%);color:var(--text);padding:36px;min-height:100vh;display:flex;justify-content:center;}
  .container{max-width:1150px;width:100%;display:flex;flex-direction:column;gap:22px}

  /* Header */
  header.site{display:flex;align-items:center;justify-content:space-between;gap:16px}
  .brand{display:flex;align-items:center;gap:16px}
  .logo{width:72px;height:72px;border-radius:16px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:22px;box-shadow:var(--shadow-lg)}
  .title-group h1{font-size:1.25rem;margin-bottom:2px}
  .subtitle{color:var(--muted);font-size:0.95rem}

  .meta{display:flex;gap:10px;align-items:center}
  .badge{background:linear-gradient(90deg,rgba(59,130,246,0.06),transparent);color:var(--accent);padding:8px 14px;border-radius:999px;font-weight:700;font-size:.95rem;border:1px solid rgba(59,130,246,0.08);box-shadow:var(--shadow-sm)}

  /* Hero */
  .hero{background:linear-gradient(180deg,rgba(255,255,255,0.7),var(--card));border-radius:var(--radius);padding:18px;border:1px solid var(--border);box-shadow:var(--shadow-sm);display:flex;justify-content:space-between;align-items:center;gap:18px}
  .hero .left{max-width:72%}
  .hero p{color:var(--muted);margin-top:6px}
  .hero .cta{display:flex;gap:10px;align-items:center}
  .btn-primary{background:linear-gradient(90deg,var(--accent),var(--accent-2));color:#fff;padding:10px 16px;border-radius:12px;border:none;cursor:pointer;font-weight:700;box-shadow:0 10px 30px rgba(59,130,246,0.12)}
  .btn-ghost{padding:8px 12px;border-radius:10px;border:1px solid var(--border);background:transparent;color:var(--muted);cursor:pointer}

  /* Controls */
  .controls{display:flex;justify-content:space-between;gap:12px;align-items:center}
  .controls-left{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
  .search{display:flex;align-items:center;gap:8px;background:linear-gradient(180deg,#fff,rgba(255,255,255,0.9));padding:8px 12px;border-radius:12px;border:1px solid var(--border);min-width:260px;box-shadow:var(--shadow-sm)}
  .search input{border:0;outline:0;background:transparent;font-size:0.95rem;color:var(--text);width:260px}
  .select{padding:8px 12px;border-radius:10px;border:1px solid var(--border);background:#fff;font-size:0.95rem}

  .controls-right{display:flex;align-items:center;gap:10px}

  /* Table card */
  .card{background:var(--card);border-radius:18px;padding:14px;border:1px solid var(--border);box-shadow:var(--shadow-lg)}
  table{width:100%;border-collapse:collapse}
  thead th{background:transparent;text-align:left;padding:16px;font-weight:700;color:var(--muted);font-size:0.95rem}
  tbody td{padding:14px;border-top:1px solid var(--border);vertical-align:middle}
  tbody tr{transition:background .18s, transform .12s}
  tbody tr:hover{background:linear-gradient(90deg,rgba(124,58,237,0.03),transparent);transform:translateY(-2px);}

  .rank{width:64px;font-weight:800;color:var(--accent);font-size:0.95rem}
  .pos-col{width:72px;text-align:center;font-weight:800}
  .user a{color:var(--accent);font-weight:700;text-decoration:none}
  .user .realname{display:block;color:var(--muted);font-size:0.86rem;margin-top:4px;font-weight:600}
  .rating{text-align:right;font-weight:800}
  .seen{text-align:right;color:var(--muted);font-weight:600}

  /* diff badges */
  .diff{font-size:0.85rem;margin-left:8px;padding:4px 8px;border-radius:999px;font-weight:700}
  .diff-pos{background:rgba(16,185,129,0.12);color:var(--green);border:1px solid rgba(16,185,129,0.16)}
  .diff-neg{background:rgba(239,68,68,0.08);color:var(--red);border:1px solid rgba(239,68,68,0.12)}
  .diff-zero{background:rgba(99,102,241,0.06);color:var(--violet);border:1px solid rgba(99,102,241,0.08)}

  /* position arrows */
  .pos-arrow {font-weight:900;margin-right:6px}
  .pos-up{color:var(--green)}
  .pos-down{color:var(--red)}
  .pos-same{color:var(--gray)}

  /* Mobile card list */
  .card-list{display:none}
  @media(max-width:880px){
    table{display:none}
    .card-list{display:flex;flex-direction:column;gap:12px}
    .player-card{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:12px;border-radius:12px;border:1px solid var(--border);background:var(--card)}
    .player-left{display:flex;gap:12px;align-items:center}
    .avatar{width:56px;height:56px;border-radius:12px;background:linear-gradient(135deg,var(--accent),var(--accent-2));display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800}
    .player-names{display:flex;flex-direction:column}
    .player-nick{font-weight:800;color:var(--accent)}
    .player-real{color:var(--muted);font-size:0.92rem;font-weight:600}
    .player-right{text-align:right;color:var(--muted);font-weight:700}
    .player-right .diff{display:inline-block;margin-top:6px}
  }

  /* pager */
  .pager{display:flex;gap:8px;justify-content:flex-end;padding-top:10px}
  .pager button{padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:#fff;cursor:pointer}

  footer{color:var(--muted);text-align:center;margin-top:6px;font-size:0.9rem}

  /* legend */
  .legend{display:flex;gap:12px;align-items:center;margin-top:8px;color:var(--muted);font-size:0.9rem}
  .legend .item{display:flex;align-items:center;gap:8px}
  .legend .sw{width:14px;height:14px;border-radius:6px}
</style>
</head>
<body>
<div class="container" id="app">
  <header class="site">
    <div class="brand">
      <a class="logo" href="./" aria-label="Ir para a página inicial">XJES</a>
      <div class="title-group">
        <h1>Ranking Xadrez Jovem ES</h1>
        <div class="subtitle">Jogadores ativos — últimos 30 dias</div>
      </div>
    </div>
    <div class="meta">
      <div class="badge" aria-live="polite">Ativos: <strong id="totalBadge">—</strong></div>
    </div>
  </header>

  <div class="hero">
    <div class="left">
      <strong>Quer aparecer no ranking?</strong>
      <p>Seja membro do time <strong>xadrezjovemes</strong> no Lichess. Aparece automaticamente após a geração diária.</p>
    </div>
    <div class="cta">
      <a class="btn-primary" href="https://lichess.org/team/xadrezjovemes" target="_blank" rel="noopener">Entrar no time</a>
      <button id="dismissJoinBanner" class="btn-ghost">Fechar</button>
    </div>
  </div>

  <div class="controls">
    <div class="controls-left">
      <div class="search" title="Pesquisar usuário">
        <input id="q" type="search" placeholder="Buscar usuário..." aria-label="Buscar usuário">
      </div>
      <select id="sort" class="select" title="Ordenar">
        <option value="blitz">Blitz</option>
        <option value="bullet">Bullet</option>
        <option value="rapid">Rapid</option>
        <option value="position">Posição</option>
        <option value="username">Usuário</option>
      </select>
      <select id="order" class="select" title="Ordem">
        <option value="desc">Desc</option>
        <option value="asc">Asc</option>
      </select>
      <select id="perPage" class="select" title="Por página">
        <option value="10">10</option>
        <option value="20" selected>20</option>
        <option value="50">50</option>
      </select>
    </div>

    <div class="controls-right">
      <button id="exportCsv" class="btn-ghost">Exportar CSV</button>
      <div id="info" class="subtitle">—</div>
    </div>
  </div>

  <div class="card">
    <div style="overflow:auto">
      <table aria-live="polite">
        <thead>
          <tr>
            <th>#</th>
            <th class="pos-col">Δ</th>
            <th>Usuário</th>
            <th style="text-align:right">Blitz</th>
            <th style="text-align:right">Bullet</th>
            <th style="text-align:right">Rapid</th>
            <th style="text-align:right">Último login</th>
          </tr>
        </thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>

    <div class="card-list" id="cardList"></div>

    <div class="legend" aria-hidden="true">
      <div class="item"><span class="sw" style="background:rgba(16,185,129,0.12);border:1px solid rgba(16,185,129,0.16)"></span>Subiu</div>
      <div class="item"><span class="sw" style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.12)"></span>Caiu</div>
      <div class="item"><span class="sw" style="background:rgba(99,102,241,0.06);border:1px solid rgba(99,102,241,0.08)"></span>Manteve</div>
    </div>

    <div class="pager" id="pager"></div>
  </div>

  <footer>Dados: Lichess · Site atualizado uma vez por dia.</footer>
</div>

<script>
(() => {
  let all = [], filtered = [], page = 1;

  const qEl = document.getElementById('q');
  const sortEl = document.getElementById('sort'), orderEl = document.getElementById('order'), perPageEl = document.getElementById('perPage');
  const infoEl = document.getElementById('info'), totalBadge = document.getElementById('totalBadge');
  const tbody = document.getElementById('tbody'), pager = document.getElementById('pager'), cardList = document.getElementById('cardList');
  const exportBtn = document.getElementById('exportCsv');

  function debounce(fn, wait=250){let t; return (...a)=>{clearTimeout(t);t=setTimeout(()=>fn(...a),wait);};}
  function formatSeen(ms){
    if(!ms) return '—';
    const ts = Number(ms) || Date.parse(ms) || 0; if(!ts) return '—';
    const diff = Date.now() - ts; if(diff < 0) return 'Agora';
    const days = Math.floor(diff/(24*3600*1000));
    if(days === 0) return 'Hoje';
    if(days === 1) return '1 dia';
    if(days < 30) return `${days} dias`;
    const months = Math.floor(days/30);
    if(months < 12) return `${months} meses`;
    return `${Math.floor(months/12)} anos`;
  }
  function safeNumber(v){ return v==null||v===''?0:Number(v)||0; }

  function formatDiff(d){
    const n = (d === null || d === undefined || d === '') ? 0 : Number(d);
    if(Number.isNaN(n)) return '(0)';
    const sign = n > 0 ? '+' : '';
    return `(${sign}${n})`;
  }

  function posBadge(p){
    if(!p || p.position_arrow == null) return '';
    const arrow = p.position_arrow;
    const change = (typeof p.position_change === 'number') ? Math.abs(p.position_change) : '';
    const cls = arrow === '▲' ? 'pos-up' : (arrow === '▼' ? 'pos-down' : 'pos-same');
    return `<div style="display:flex;align-items:center;justify-content:center"><span class="pos-arrow ${cls}">${escapeHtml(arrow)}</span>${change ? `<span style="font-weight:700">${change}</span>` : ''}</div>`;
  }

  function extractPlayers(obj){
    const found = [];
    if(!obj) return found;
    if(Array.isArray(obj)) return obj.slice();
    if(Array.isArray(obj.players)) found.push(...obj.players);
    Object.values(obj).forEach(v=>{
      if(Array.isArray(v)) found.push(...v);
    });
    return found;
  }

  function dedupePlayers(list){
    const map = new Map();
    const score = p => safeNumber(p.blitz) + safeNumber(p.bullet) + safeNumber(p.rapid);
    list.forEach(p=>{
      const key = (p.username||'').toString().trim().toLowerCase();
      if(!key){
        const id = `__anon_${Math.random().toString(36).slice(2,9)}`;
        map.set(id, Object.assign({},p,{username:id}));
        return;
      }
      if(!map.has(key)) map.set(key, p);
      else {
        const existing = map.get(key);
        if(score(p) > score(existing)) map.set(key,p);
        else {
          const eSeen = Date.parse(existing.seenAt||'')||0;
          const pSeen = Date.parse(p.seenAt||'')||0;
          if(pSeen > eSeen) map.set(key,p);
        }
      }
    });
    return Array.from(map.values());
  }

  async function loadData(force=false){
    infoEl.textContent = 'Carregando...';
    try{
      // cache-busting
      const url = './players.json?v=' + Date.now();
      const res = await fetch(url, {cache: 'no-store'});
      if(!res.ok) throw new Error('Falha ao buscar players.json: ' + res.status);
      const js = await res.json();

      let candidates = extractPlayers(js);
      if(candidates.length === 0 && Array.isArray(js.players)) candidates = js.players.slice();
      all = dedupePlayers(candidates);

      if(all.length && all[0].position != null){
        all.sort((a,b)=> (safeNumber(a.position) - safeNumber(b.position)));
      }else{
        all.sort((a,b)=> (safeNumber(b.blitz) - safeNumber(a.blitz)));
      }

      totalBadge.textContent = all.length;
      const gen = js.generated_at ? new Date(Number(js.generated_at)).toLocaleString() : '—';
      infoEl.textContent = `Total: ${all.length} — gerado: ${gen}`;
      page = 1;
      applyFilters();
    }catch(err){
      console.error(err);
      infoEl.textContent = 'Erro ao carregar dados';
      tbody.innerHTML = '<tr><td colspan="7" style="padding:18px;text-align:center;color:#6b7280">Erro ao carregar players.json</td></tr>';
    }
  }

  function applyFilters(){
    const q = (qEl.value || '').trim().toLowerCase();
    filtered = all.filter(p => {
      if(!q) return true;
      return (p.username||'').toString().toLowerCase().includes(q) || (p.name||p.realname||'').toString().toLowerCase().includes(q);
    });

    const key = sortEl.value, ord = orderEl.value === 'asc' ? 1 : -1;
    filtered.sort((a,b)=>{
      if(key === 'username'){ const A=(a.username||'').toLowerCase(), B=(b.username||'').toLowerCase(); return (A < B ? -1 : A > B ? 1 : 0) * ord; }
      if(key === 'position'){
        const pa = a.position == null ? Infinity : Number(a.position);
        const pb = b.position == null ? Infinity : Number(b.position);
        return (pa < pb ? -1 : pa > pb ? 1 : 0) * ord;
      }
      const va = safeNumber(a[key]), vb = safeNumber(b[key]);
      return (va < vb ? -1 : va > vb ? 1 : 0) * ord;
    });
    page = 1;
    renderPage();
  }

  function renderPage(){
    const per = Math.max(1, parseInt(perPageEl.value,10) || 20);
    const totalPages = Math.max(1, Math.ceil(filtered.length / per));
    if(page > totalPages) page = totalPages;
    const start = (page - 1) * per;
    const items = filtered.slice(start, start + per);

    if(items.length === 0){
      tbody.innerHTML = '<tr><td colspan="7" style="padding:18px;text-align:center;color:#6b7280">Nenhum jogador encontrado</td></tr>';
    } else {
      const rows = items.map((p,idx)=>{
        const rank = start + idx + 1;
        const real = p.name || p.realname || p.fullname || '';
        const profile = (p.profile || p.url) ? (p.profile || p.url) : `https://lichess.org/@/${encodeURIComponent(p.username||'')}`;
        const userEsc = escapeHtml(p.username||'(sem usuário)');
        const realHtml = real ? `<span class="realname">${escapeHtml(real)}</span>` : `<span class="realname">Sem nome registrado</span>`;
        return `<tr>
          <td class="rank">${rank}</td>
          <td class="pos-col">${posBadge(p)}</td>
          <td class="user"><a href="${profile}" target="_blank" rel="noopener">${userEsc}</a>${realHtml}</td>
          <td class="rating">${p.blitz ?? '—'} <span class="${(p.blitz_diff>0)?'diff diff-pos':(p.blitz_diff<0)?'diff diff-neg':'diff diff-zero'}">${formatDiff(p.blitz_diff)}</span></td>
          <td class="rating">${p.bullet ?? '—'} <span class="${(p.bullet_diff>0)?'diff diff-pos':(p.bullet_diff<0)?'diff diff-neg':'diff diff-zero'}">${formatDiff(p.bullet_diff)}</span></td>
          <td class="rating">${p.rapid ?? '—'} <span class="${(p.rapid_diff>0)?'diff diff-pos':(p.rapid_diff<0)?'diff diff-neg':'diff diff-zero'}">${formatDiff(p.rapid_diff)}</span></td>
          <td class="seen">${formatSeen(p.seenAt)}</td>
        </tr>`;
      }).join('');
      tbody.innerHTML = rows;
    }

    cardList.innerHTML = '';
    items.forEach((p, idx) => {
      const rank = start + idx + 1;
      const real = p.name || p.realname || p.fullname || 'Sem nome registrado';
      const profile = (p.profile || p.url) ? (p.profile || p.url) : `https://lichess.org/@/${encodeURIComponent(p.username||'')}`;
      const initial = escapeHtml((p.username||'').charAt(0).toUpperCase());
      const userEsc = escapeHtml(p.username||'(sem usuário)');
      const realEsc = real ? escapeHtml(real) : '';
      const posHtml = p.position_arrow ? `<span class="pos-arrow ${p.position_arrow==='▲'?'pos-up':p.position_arrow==='▼'?'pos-down':'pos-same'}">${p.position_arrow}</span>` : '';
      const html = `<div class="player-card">
        <div class="player-left">
          <a href="${profile}" target="_blank" rel="noopener" style="display:flex;gap:12px;align-items:center;text-decoration:none;color:inherit">
            <div class="avatar" aria-hidden="true">${initial}</div>
            <div class="player-names">
              <div class="player-nick">${rank}. ${userEsc}</div>
              <div class="player-real">${realEsc}</div>
            </div>
          </a>
        </div>
        <div class="player-right">
          <div>Pos: ${p.position ?? rank} ${posHtml} ${p.position_change != null ? Math.abs(p.position_change) : ''}</div>
          <div>Blitz: ${p.blitz ?? '—'} <span class="${(p.blitz_diff>0)?'diff diff-pos':(p.blitz_diff<0)?'diff diff-neg':'diff diff-zero'}">${formatDiff(p.blitz_diff)}</span></div>
          <div>Bullet: ${p.bullet ?? '—'} <span class="${(p.bullet_diff>0)?'diff diff-pos':(p.bullet_diff<0)?'diff diff-neg':'diff diff-zero'}">${formatDiff(p.bullet_diff)}</span></div>
          <div>Rapid: ${p.rapid ?? '—'} <span class="${(p.rapid_diff>0)?'diff diff-pos':(p.rapid_diff<0)?'diff diff-neg':'diff diff-zero'}">${formatDiff(p.rapid_diff)}</span></div>
          <div style="font-size:.85rem;color:var(--muted);margin-top:6px">${formatSeen(p.seenAt)}</div>
        </div>
      </div>`;
      cardList.insertAdjacentHTML('beforeend', html);
    });

    renderPager(per, totalPages);
  }

  function renderPager(per, totalPages){
    pager.innerHTML = '';
    const make = (label, to, disabled=false) => {
      const b = document.createElement('button'); b.textContent = label; b.disabled = disabled;
      b.onclick = () => { page = to; renderPage(); window.scrollTo({top:0,behavior:'smooth'}); };
      return b;
    };
    pager.appendChild(make('«',1,page===1));
    pager.appendChild(make('‹', Math.max(1,page-1), page===1));
    const start = Math.max(1, page-2), end = Math.min(totalPages, page+2);
    for(let i=start;i<=end;i++){
      const btn = make(String(i), i, i===page);
      if(i===page){ btn.style.fontWeight='700'; btn.style.background = 'linear-gradient(90deg,var(--accent),var(--accent-2))'; btn.style.color='#fff'; btn.style.border='none'; }
      pager.appendChild(btn);
    }
    pager.appendChild(make('›', Math.min(totalPages, page+1), page===totalPages));
    pager.appendChild(make('»', totalPages, page===totalPages));
  }

  function exportCurrentPageCsv(){
    const per = Math.max(1, parseInt(perPageEl.value,10)||20), start = (page-1)*per;
    const items = filtered.slice(start, start+per);
    if(!items.length){ alert('Nenhum item para exportar'); return; }
    const headers = ['#','username','name','profile','position','position_change','position_arrow','blitz','blitz_diff','bullet','bullet_diff','rapid','rapid_diff','seenAt'];
    const rows = items.map((p,idx)=>[
      start+idx+1,
      p.username||'',
      p.name||p.realname||'',
      p.profile||'',
      p.position ?? '',
      (p.position_change != null ? p.position_change : ''),
      p.position_arrow || '',
      p.blitz || '',
      p.blitz_diff != null ? p.blitz_diff : 0,
      p.bullet || '',
      p.bullet_diff != null ? p.bullet_diff : 0,
      p.rapid || '',
      p.rapid_diff != null ? p.rapid_diff : 0,
      p.seenAt || ''
    ]);
    const csv = [headers.join(','), ...rows.map(r=>r.map(v=>`"${String(v).replace(/"/g,'""')}"`).join(','))].join('\r\n');
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv],{type:'text/csv'})); a.download = `ranking_page${page}.csv`; a.click();
  }

  function escapeHtml(s){ return String(s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  const debouncedFilter = debounce(applyFilters, 250);
  qEl.addEventListener('input', debouncedFilter);
  sortEl.addEventListener('change', applyFilters);
  orderEl.addEventListener('change', applyFilters);
  perPageEl.addEventListener('change', renderPage);
  exportBtn.addEventListener('click', exportCurrentPageCsv);
  document.getElementById('dismissJoinBanner').addEventListener('click', ()=>document.querySelector('.hero').style.display='none');

  loadData();
})();
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
    print("Abra http://127.0.0.1:8000 no navegador")
    app.run(host="127.0.0.1", port=8000, debug=False)
