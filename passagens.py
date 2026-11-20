#!/usr/bin/env python3
"""Alerta de passagens baratas.

Busca preços de voos (via Google Flights, usando o SerpApi), guarda o histórico
e manda alerta no Telegram quando o preço fica abaixo do seu alvo ou cai bastante.

Uso rápido:
  python passagens.py buscar CNF CWB 2027-01-15 [--volta 2027-01-20]
  python passagens.py adicionar CNF CWB 2027-01-15 --max 400 [--volta ...]
  python passagens.py adicionar CNF CWB 2027-01-10:2027-01-20 --max 400   (faixa de datas)
  python passagens.py listar
  python passagens.py remover 1
  python passagens.py verificar          (roda todos os alertas uma vez)
  python passagens.py bot                (bot do Telegram com comandos /buscar, /alertar...)
  python passagens.py mensagens          (responde as mensagens novas do Telegram e sai; usado no GitHub)

Variáveis de ambiente:
  SERPAPI_KEY         chave do SerpApi (https://serpapi.com)  -> sem ela usa dados de exemplo
  TELEGRAM_TOKEN      token do bot (fale com @BotFather no Telegram)
  TELEGRAM_CHAT_ID    seu chat id (opcional: se faltar, quem falar primeiro com o bot vira o dono)
  QUEDA_MINIMA_PCT    queda em % vs última busca que também gera alerta (padrão 10)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parent

# Tira espaços/quebras de linha que às vezes vêm junto ao colar as chaves.
for _k in ("SERPAPI_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
    if _k in os.environ:
        os.environ[_k] = os.environ[_k].strip()
        if not os.environ[_k]:
            del os.environ[_k]
ALERTAS_FILE = Path(os.environ.get("ALERTAS_FILE", BASE / "alertas.json"))
HISTORICO_FILE = Path(os.environ.get("HISTORICO_FILE", BASE / "historico.json"))
ESTADO_FILE = Path(os.environ.get("ESTADO_FILE", BASE / "estado.json"))

def aeroporto(txt: str) -> str:
    """'Tóquio', 'japao', 'NRT', 'HND,NRT', 'NRT · Tóquio' ou 'Tóquio (HND,NRT)' -> código(s) IATA."""
    import re
    from cidades import INDICE, normalizar
    t = txt.strip()
    m = re.match(r"^([A-Za-z]{3}(?:,[A-Za-z]{3})*)\s*·", t) or re.search(r"\(([A-Za-z]{3}(?:,[A-Za-z]{3})*)\)\s*$", t)
    if m:
        return m.group(1).upper()
    if normalizar(t) in INDICE:
        return INDICE[normalizar(t)]
    if re.fullmatch(r"[A-Za-z]{3}(,[A-Za-z]{3})*", t):
        return t.upper()
    raise ValueError(f"Não conheço \"{t}\". Escolha uma cidade da lista ou digite o código do aeroporto "
                     "com 3 letras (ex.: NRT para Tóquio).")


def brl(v: float | None) -> str:
    if v is None:
        return "—"
    return "R$ " + f"{v:,.0f}".replace(",", ".")


def data_br(iso: str) -> str:
    return dt.date.fromisoformat(iso).strftime("%d/%m/%Y")


# --------------------------------------------------------------------------
# Fontes de preço
# --------------------------------------------------------------------------

def buscar_voos(origem: str, destino: str, ida: str, volta: str | None = None) -> dict:
    """Retorna {"voos": [...], "link": str, "insight": str|None, "fonte": str}.

    Cada voo: {"preco", "cias", "saida", "chegada", "duracao_min", "escalas"}.
    """
    if os.environ.get("SERPAPI_KEY"):
        return _serpapi(origem, destino, ida, volta)
    return _exemplo(origem, destino, ida, volta)


def _serpapi_get(origem, destino, ida, volta, **extra):
    params = {
        "engine": "google_flights",
        "departure_id": origem,
        "arrival_id": destino,
        "outbound_date": ida,
        "type": "1" if volta else "2",  # 1 = ida e volta, 2 = só ida
        "currency": "BRL",
        "hl": "pt-br",
        "gl": "br",
        "api_key": os.environ["SERPAPI_KEY"],
    }
    if volta:
        params["return_date"] = volta
    params.update(extra)
    url = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
    if "error" in data:
        raise RuntimeError(f"SerpApi: {data['error']}")
    return data


def _ler_voos(data):
    voos = []
    for grupo in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        trechos = grupo.get("flights") or []
        if not trechos or grupo.get("price") is None:
            continue
        voos.append({
            "preco": float(grupo["price"]),
            "cias": sorted({t.get("airline", "?") for t in trechos}),
            "saida": trechos[0]["departure_airport"].get("time", ""),
            "chegada": trechos[-1]["arrival_airport"].get("time", ""),
            "duracao_min": grupo.get("total_duration"),
            "escalas": len(trechos) - 1,
            "booking_token": grupo.get("booking_token"),
            "departure_token": grupo.get("departure_token"),
        })
    voos.sort(key=lambda v: v["preco"])
    return voos


def _serpapi(origem, destino, ida, volta):
    data = _serpapi_get(origem, destino, ida, volta)
    voos = _ler_voos(data)

    insight = None
    pi = data.get("price_insights") or {}
    if pi.get("price_level"):
        nivel = {"low": "baixo", "typical": "normal", "high": "alto"}.get(pi["price_level"], pi["price_level"])
        faixa = pi.get("typical_price_range")
        insight = f"Google diz que o preço está {nivel}"
        if faixa and len(faixa) == 2:
            insight += f" (normal: {brl(faixa[0])} a {brl(faixa[1])})"
    link = (data.get("search_metadata") or {}).get("google_flights_url") or _link_google(origem, destino, ida, volta)
    return {"voos": voos, "link": link, "insight": insight, "fonte": "Google Flights (SerpApi)"}


def _exemplo(origem, destino, ida, volta):
    """Dados falsos, só pra testar sem chave. Varia um pouco a cada chamada."""
    semente = int(hashlib.md5(f"{origem}{destino}{ida}{volta}".encode()).hexdigest()[:8], 16)
    rnd = random.Random(semente + int(time.time() // 3600))
    base = 250 + semente % 400
    cias = ["GOL", "Azul", "LATAM"]
    voos = []
    for i in range(6):
        h = 6 + i * 2 + rnd.randint(0, 1)
        dur = rnd.choice([75, 90, 160, 210])
        voos.append({
            "preco": float(round(base * rnd.uniform(0.75, 1.6) * (1.8 if volta else 1))),
            "cias": [rnd.choice(cias)],
            "saida": f"{ida} {h:02d}:{rnd.choice(['00', '15', '40'])}",
            "chegada": "",
            "duracao_min": dur,
            "escalas": 0 if dur < 100 else 1,
        })
    voos.sort(key=lambda v: v["preco"])
    return {"voos": voos, "link": _link_google(origem, destino, ida, volta),
            "insight": None, "fonte": "DADOS DE EXEMPLO (configure SERPAPI_KEY)"}


def _link_google(origem, destino, ida, volta):
    origem, destino = origem.split(",")[0], destino.split(",")[0]
    q = f"voos de {origem} para {destino} em {ida}" + (f" volta {volta}" if volta else " só ida")
    return "https://www.google.com/travel/flights?hl=pt-BR&curr=BRL&q=" + urllib.parse.quote(q)


def ofertas(origem, destino, ida, volta=None, booking_token=None, departure_token=None) -> list[dict]:
    """Quem vende esse voo e o link pra comprar (via Google Flights / SerpApi).

    Ida e volta: o 1º token (departure_token) escolhe a ida; busca a volta mais barata
    e pega o booking_token dela. Gasta 1 busca (só ida) ou 2 (ida e volta).
    """
    if not os.environ.get("SERPAPI_KEY"):
        return []
    if not booking_token and departure_token:
        voltas = _ler_voos(_serpapi_get(origem, destino, ida, volta, departure_token=departure_token))
        booking_token = next((v["booking_token"] for v in voltas if v.get("booking_token")), None)
    if not booking_token:
        return []
    data = _serpapi_get(origem, destino, ida, volta, booking_token=booking_token)
    saida = []
    for op in data.get("booking_options") or []:
        partes = [op[k] for k in ("together", "departing", "returning") if op.get(k)]
        for parte in partes[:1]:  # "together" = um bilhete só; senão mostra o da ida
            req = parte.get("booking_request") or {}
            if not req.get("url"):
                continue
            saida.append({
                "vendedor": parte.get("book_with") or "?",
                "cia": bool(parte.get("airline")),
                "preco": parte.get("price"),
                "titulo": parte.get("option_title"),
                "bagagem": parte.get("baggage_prices") or [],
                "url": req["url"],
                "post_data": req.get("post_data") or "",
                "separado": "together" not in op,
            })
    saida.sort(key=lambda o: (o["preco"] is None, o["preco"] or 0))
    return saida


def links_cias(origem, destino, ida, volta=None) -> dict:
    """Busca já preenchida direto no site das companhias (não garante o mesmo preço)."""
    o, d = origem.split(",")[0], destino.split(",")[0]
    di = dt.date.fromisoformat(ida)
    dv = dt.date.fromisoformat(volta) if volta else None
    latam = (f"https://www.latamairlines.com/br/pt/oferta-voos?origin={o}&destination={d}"
             f"&outbound={ida}T12%3A00%3A00.000Z&adt=1&chd=0&inf=0&cabin=Economy&redemption=false"
             + (f"&inbound={volta}T12%3A00%3A00.000Z&trip=RT" if volta else "&trip=OW"))
    azul = (f"https://www.voeazul.com.br/br/pt/home/selecao-voo?c[0].ds={o}&c[0].std={di:%m/%d/%Y}&c[0].as={d}"
            + (f"&c[1].ds={d}&c[1].std={dv:%m/%d/%Y}&c[1].as={o}" if dv else "")
            + "&p[0].t=ADT&p[0].c=1&p[0].cp=false&f.dl=3&f.dr=3&cc=BRL")
    gol = (f"https://b2c.voegol.com.br/compra/busca-parceiros?pv=br&tipo={'RT' if volta else 'OW'}"
           f"&de={o}&para={d}&ida={di:%d-%m-%Y}" + (f"&volta={dv:%d-%m-%Y}" if dv else "") + "&ADT=1&CHD=0&INF=0")
    return {"LATAM": latam, "Azul": azul, "GOL": gol}


def link_cia(nome_cia, origem, destino, ida, volta=None):
    n = nome_cia.lower()
    links = links_cias(origem, destino, ida, volta)
    for chave in links:
        if chave.lower() in n:
            return chave, links[chave]
    return None


def buscar_datas(origem, destino, datas_ida, volta=None) -> dict:
    """Busca várias datas em paralelo. Retorna {data: resultado} (datas sem voo ficam de fora)."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(8, len(datas_ida))) as ex:
        res = list(ex.map(lambda d: buscar_voos(origem, destino, d, volta), datas_ida))
    return {d: r for d, r in zip(datas_ida, res) if r["voos"]}


def melhor_preco(origem, destino, datas_ida, volta=None):
    """Busca em todas as datas e devolve (data, resultado) com o menor preço."""
    todos = buscar_datas(origem, destino, datas_ida, volta)
    if not todos:
        return None
    d = min(todos, key=lambda k: todos[k]["voos"][0]["preco"])
    return d, todos[d]


def links_outros_sites(origem, destino, ida, volta=None) -> dict:
    """Links prontos pra conferir a mesma busca em outros sites."""
    o, d = origem.split(",")[0], destino.split(",")[0]
    yymmdd = lambda x: dt.date.fromisoformat(x).strftime("%y%m%d")  # noqa: E731
    sky = f"https://www.skyscanner.com.br/transporte/passagens-aereas/{o.lower()}/{d.lower()}/{yymmdd(ida)}/"
    kayak = f"https://www.kayak.com.br/flights/{o}-{d}/{ida}"
    if volta:
        sky += f"{yymmdd(volta)}/"
        kayak += f"/{volta}"
    return {
        "Google Flights": _link_google(origem, destino, ida, volta),
        "Skyscanner": sky,
        "Kayak": kayak + "?sort=price_a",
    }


# --------------------------------------------------------------------------
# Armazenamento (JSON simples, fácil de editar à mão)
# --------------------------------------------------------------------------

def _ler(path: Path, padrao):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return padrao


def _gravar(path: Path, dados):
    path.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


# Quando roda no site (Vercel), os alertas ficam no próprio repositório do GitHub,
# lidos e gravados pela API. Assim o site e o robô do GitHub Actions usam a mesma lista.
GH_TOKEN = os.environ.get("GITHUB_ALERTAS_TOKEN", "").strip()
GH_REPO = os.environ.get("GITHUB_REPO", "").strip()
_gh_sha: dict[str, str] = {}


def _gh(metodo, caminho, corpo=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GH_REPO}/contents/{caminho}",
        data=json.dumps(corpo).encode() if corpo else None, method=metodo,
        headers={"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json",
                 "User-Agent": "alerta-passagens"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _ler_repo(nome, padrao):
    import base64
    try:
        d = _gh("GET", nome)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return padrao
        raise
    _gh_sha[nome] = d["sha"]
    return json.loads(base64.b64decode(d["content"]).decode("utf-8"))


def _gravar_repo(nome, dados, mensagem):
    import base64
    corpo = {"message": mensagem,
             "content": base64.b64encode(json.dumps(dados, ensure_ascii=False, indent=2).encode()).decode()}
    if nome in _gh_sha:
        corpo["sha"] = _gh_sha[nome]
    _gh_sha[nome] = _gh("PUT", nome, corpo)["content"]["sha"]


def carregar_alertas() -> list[dict]:
    if GH_TOKEN and GH_REPO:
        return _ler_repo("alertas.json", [])
    return _ler(ALERTAS_FILE, [])


def salvar_alertas(alertas):
    if GH_TOKEN and GH_REPO:
        return _gravar_repo("alertas.json", alertas, "Atualiza alertas pelo site")
    _gravar(ALERTAS_FILE, alertas)


def carregar_historico() -> dict:
    if GH_TOKEN and GH_REPO:
        return _ler_repo("historico.json", {})
    return _ler(HISTORICO_FILE, {})


def expandir_datas(spec: str) -> list[str]:
    """'2027-01-15' ou '2027-01-10:2027-01-20' (no máx. 15 dias)."""
    if ":" in spec:
        a, b = (dt.date.fromisoformat(x) for x in spec.split(":"))
        dias = (b - a).days
        if dias < 0 or dias > 14:
            raise ValueError("Faixa de datas deve ter de 1 a 15 dias.")
        return [(a + dt.timedelta(days=i)).isoformat() for i in range(dias + 1)]
    dt.date.fromisoformat(spec)
    return [spec]


def adicionar_alerta(origem, destino, datas, preco_max, volta=None) -> dict:
    expandir_datas(datas)  # valida
    alertas = carregar_alertas()
    novo = {
        "id": max([a["id"] for a in alertas], default=0) + 1,
        "origem": aeroporto(origem),
        "destino": aeroporto(destino),
        "datas": datas,
        "volta": volta,
        "preco_max": float(preco_max) if preco_max else None,
        "criado_em": dt.datetime.now().isoformat(timespec="seconds"),
    }
    alertas.append(novo)
    salvar_alertas(alertas)
    return novo


def remover_alerta(id_: int) -> bool:
    alertas = carregar_alertas()
    restantes = [a for a in alertas if a["id"] != id_]
    salvar_alertas(restantes)
    return len(restantes) != len(alertas)


def descrever_alerta(a) -> str:
    datas = a["datas"]
    if ":" in datas:
        x, y = datas.split(":")
        quando = f"entre {data_br(x)} e {data_br(y)}"
    else:
        quando = f"em {data_br(datas)}"
    volta = f", volta {data_br(a['volta'])}" if a.get("volta") else " (só ida)"
    alvo = f", avisar abaixo de {brl(a['preco_max'])}" if a.get("preco_max") else ""
    return f"#{a['id']} {a['origem']} → {a['destino']} {quando}{volta}{alvo}"


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------

def telegram(metodo: str, **params):
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Falta TELEGRAM_TOKEN")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{metodo}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.load(r)["result"]


def chat_dono():
    return os.environ.get("TELEGRAM_CHAT_ID") or _ler(ESTADO_FILE, {}).get("dono")


def enviar(texto: str, chat_id=None):
    chat_id = chat_id or chat_dono()
    if not (os.environ.get("TELEGRAM_TOKEN") and chat_id):
        print("\n[Telegram não configurado, mostrando aqui]\n" + texto + "\n")
        return
    telegram("sendMessage", chat_id=chat_id, text=texto, parse_mode="HTML",
             disable_web_page_preview=True)


def formatar_resultado(origem, destino, ida, volta, res, limite=5) -> str:
    cab = f"✈️ <b>{origem} → {destino}</b> em {data_br(ida)}"
    cab += f" (volta {data_br(volta)})" if volta else " (só ida)"
    linhas = [cab]
    if not res["voos"]:
        linhas.append("Nenhum voo encontrado.")
    for v in res["voos"][:limite]:
        hora = v["saida"][-5:] if v["saida"] else ""
        esc = "direto" if v["escalas"] == 0 else f"{v['escalas']} escala(s)"
        dur = f"{v['duracao_min'] // 60}h{v['duracao_min'] % 60:02d}" if v.get("duracao_min") else ""
        linhas.append(f"• <b>{brl(v['preco'])}</b> {', '.join(v['cias'])} {hora} · {esc} {dur}".rstrip())
    if res.get("insight"):
        linhas.append(f"📊 {res['insight']}")
    outros = links_outros_sites(origem, destino, ida, volta)
    outros["Google Flights"] = res["link"]
    if res["voos"]:
        lc = link_cia(" ".join(res["voos"][0]["cias"]), origem, destino, ida, volta)
        if lc:
            outros = {f"Comprar na {lc[0]}": lc[1], **outros}
    linhas.append(" · ".join(f'<a href="{u}">{n}</a>' for n, u in outros.items()))
    linhas.append(f"fonte: {res['fonte']}")
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# Verificação dos alertas
# --------------------------------------------------------------------------

def verificar(enviar_sempre=False) -> list[str]:
    """Roda todos os alertas, grava histórico e manda avisos. Retorna mensagens enviadas."""
    queda_min = float(os.environ.get("QUEDA_MINIMA_PCT", "10"))
    historico = _ler(HISTORICO_FILE, {})
    enviadas = []
    agora = dt.datetime.now().isoformat(timespec="minutes")
    hoje = dt.date.today().isoformat()

    for a in carregar_alertas():
        datas = [d for d in expandir_datas(a["datas"]) if d >= hoje]
        if not datas:
            continue
        try:
            achado = melhor_preco(a["origem"], a["destino"], datas, a.get("volta"))
        except Exception as e:  # noqa: BLE001
            print(f"Erro no alerta #{a['id']}: {e}", file=sys.stderr)
            continue
        if not achado:
            continue
        data, res = achado
        preco = res["voos"][0]["preco"]
        chave = str(a["id"])
        h = historico.setdefault(chave, {"registros": [], "ultimo_alerta": None})
        anterior = h["registros"][-1]["preco"] if h["registros"] else None
        minimo_hist = min([r["preco"] for r in h["registros"]], default=None)
        h["registros"].append({"quando": agora, "data": data, "preco": preco})
        h["registros"] = h["registros"][-200:]

        motivos = []
        if a.get("preco_max") and preco <= a["preco_max"]:
            # não repete o mesmo aviso se o preço não melhorou
            if h["ultimo_alerta"] is None or preco < h["ultimo_alerta"]:
                motivos.append(f"abaixo do seu alvo de {brl(a['preco_max'])}")
        if anterior and preco <= anterior * (1 - queda_min / 100):
            motivos.append(f"caiu {100 * (1 - preco / anterior):.0f}% desde a última busca ({brl(anterior)})")
        if minimo_hist and preco < minimo_hist and not motivos:
            motivos.append(f"menor preço já visto (antes {brl(minimo_hist)})")

        print(f"#{a['id']} {a['origem']}->{a['destino']} {data}: {brl(preco)} "
              f"{'-> ALERTA: ' + '; '.join(motivos) if motivos else ''}")
        if motivos or enviar_sempre:
            msg = ("🔔 <b>Passagem barata!</b> " + "; ".join(motivos) + "\n" if motivos else "")
            msg += formatar_resultado(a["origem"], a["destino"], data, a.get("volta"), res, limite=3)
            enviar(msg)
            enviadas.append(msg)
            if motivos:
                h["ultimo_alerta"] = preco
        elif a.get("preco_max") and preco > a["preco_max"] and h["ultimo_alerta"]:
            h["ultimo_alerta"] = None  # preço subiu de novo; próximo mergulho volta a avisar

    _gravar(HISTORICO_FILE, historico)
    return enviadas


# --------------------------------------------------------------------------
# Bot interativo
# --------------------------------------------------------------------------

AJUDA = """Comandos:
/buscar ORIGEM DESTINO DATA [VOLTA]
   ex: /buscar bh curitiba 2027-01-15
   ex: /buscar CNF CWB 2027-01-10:2027-01-20  (acha o dia mais barato)
/alertar ORIGEM DESTINO DATA PRECO_MAX [VOLTA]
   ex: /alertar bh curitiba 2027-01-15 400
/lista  — seus alertas
/remover N  — apaga o alerta N
/verificar  — roda os alertas agora
Datas no formato AAAA-MM-DD ou DD/MM/AAAA."""


def _data(txt: str) -> str:
    """Aceita 2027-01-15, 15/01/2027, 15/01 e faixas com ':'."""
    if ":" in txt:
        return ":".join(_data(p) for p in txt.split(":"))
    if "/" in txt:
        partes = txt.split("/")
        d, m = int(partes[0]), int(partes[1])
        if len(partes) > 2:
            y = int(partes[2])
            y = y + 2000 if y < 100 else y
        else:
            hoje = dt.date.today()
            y = hoje.year + (1 if (m, d) < (hoje.month, hoje.day) else 0)
        return dt.date(y, m, d).isoformat()
    return dt.date.fromisoformat(txt).isoformat()


def tratar_comando(texto: str) -> str:
    partes = texto.strip().split()
    if not partes:
        return AJUDA
    cmd, args = partes[0].split("@")[0].lower(), partes[1:]
    try:
        if cmd in ("/start", "/ajuda", "/help"):
            return "Olá! Eu vigio passagens baratas pra você.\n\n" + AJUDA
        if cmd == "/buscar":
            if len(args) < 3:
                return "Uso: /buscar ORIGEM DESTINO DATA [VOLTA]"
            o, d = aeroporto(args[0]), aeroporto(args[1])
            datas = expandir_datas(_data(args[2]))
            volta = _data(args[3]) if len(args) > 3 else None
            achado = melhor_preco(o, d, datas, volta)
            if not achado:
                return "Não achei voos para essa busca."
            data, res = achado
            extra = f"Dia mais barato entre {len(datas)} datas:\n" if len(datas) > 1 else ""
            return extra + formatar_resultado(o, d, data, volta, res)
        if cmd == "/alertar":
            if len(args) < 4:
                return "Uso: /alertar ORIGEM DESTINO DATA PRECO_MAX [VOLTA]"
            volta = _data(args[4]) if len(args) > 4 else None
            a = adicionar_alerta(args[0], args[1], _data(args[2]), args[3].replace("R$", ""), volta)
            return "✅ Alerta criado:\n" + descrever_alerta(a)
        if cmd in ("/lista", "/listar"):
            alertas = carregar_alertas()
            return "\n".join(descrever_alerta(a) for a in alertas) or "Nenhum alerta ainda."
        if cmd == "/remover":
            return "🗑️ Removido." if args and remover_alerta(int(args[0])) else "Não achei esse alerta."
        if cmd == "/verificar":
            n = len(verificar())
            return f"Verificação feita. {n} alerta(s) disparado(s)." if n else "Verifiquei tudo, nada abaixo do alvo por enquanto."
    except Exception as e:  # noqa: BLE001
        return f"⚠️ Erro: {e}"
    return AJUDA


def processar_mensagens(espera: int = 0) -> int:
    """Lê mensagens novas do Telegram e responde. Guarda offset e dono em estado.json.

    Se TELEGRAM_CHAT_ID não estiver definido, a primeira pessoa que falar com o bot vira o dono.
    """
    estado = _ler(ESTADO_FILE, {})
    updates = telegram("getUpdates", timeout=espera, offset=estado.get("offset"))
    n = 0
    for u in updates:
        estado["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        chat = str((m.get("chat") or {}).get("id", ""))
        if not m.get("text") or not chat:
            continue
        dono = os.environ.get("TELEGRAM_CHAT_ID") or estado.get("dono")
        if not dono:
            estado["dono"] = dono = chat
            _gravar(ESTADO_FILE, estado)
        if chat != str(dono):
            continue  # só responde ao dono
        _gravar(ESTADO_FILE, estado)  # grava antes, pra não repetir se der erro
        enviar(tratar_comando(m["text"]), chat_id=chat)
        n += 1
    _gravar(ESTADO_FILE, estado)
    return n


def rodar_bot(intervalo_verificacao_h: float = 6):
    print("Bot rodando. Ctrl+C para parar.")
    proxima_verif = time.time()
    while True:
        if time.time() >= proxima_verif:
            verificar()
            proxima_verif = time.time() + intervalo_verificacao_h * 3600
        try:
            processar_mensagens(espera=50)
        except Exception as e:  # noqa: BLE001
            print("Erro Telegram:", e, file=sys.stderr)
            time.sleep(10)


def mostrar_chat_id():
    ups = telegram("getUpdates")
    if not ups:
        print("Mande qualquer mensagem pro seu bot no Telegram e rode de novo.")
    for u in ups:
        c = (u.get("message") or {}).get("chat") or {}
        if c:
            print(f"TELEGRAM_CHAT_ID={c['id']}  ({c.get('first_name', '')} {c.get('username', '')})")


# --------------------------------------------------------------------------
# Linha de comando
# --------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description="Alerta de passagens baratas")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("buscar", help="busca agora")
    b.add_argument("origem"); b.add_argument("destino"); b.add_argument("data")
    b.add_argument("--volta")

    ad = sub.add_parser("adicionar", help="cria alerta")
    ad.add_argument("origem"); ad.add_argument("destino"); ad.add_argument("data")
    ad.add_argument("--max", type=float, help="preço máximo (R$)")
    ad.add_argument("--volta")

    sub.add_parser("listar")
    r = sub.add_parser("remover"); r.add_argument("id", type=int)
    v = sub.add_parser("verificar"); v.add_argument("--sempre", action="store_true",
                                                    help="manda o preço atual mesmo sem queda")
    bt = sub.add_parser("bot"); bt.add_argument("--horas", type=float, default=6,
                                                help="intervalo entre verificações automáticas")
    sub.add_parser("chatid")
    sub.add_parser("mensagens", help="responde mensagens novas do Telegram uma vez e sai")

    a = p.parse_args(argv)
    if a.cmd == "buscar":
        volta = _data(a.volta) if a.volta else None
        print(tratar_comando(f"/buscar {a.origem} {a.destino} {a.data}" + (f" {volta}" if volta else ""))
              .replace("<b>", "").replace("</b>", ""))
    elif a.cmd == "adicionar":
        al = adicionar_alerta(a.origem, a.destino, _data(a.data), a.max, _data(a.volta) if a.volta else None)
        print("Alerta criado:", descrever_alerta(al))
    elif a.cmd == "listar":
        for al in carregar_alertas():
            print(descrever_alerta(al))
    elif a.cmd == "remover":
        print("Removido." if remover_alerta(a.id) else "Não encontrado.")
    elif a.cmd == "verificar":
        verificar(enviar_sempre=a.sempre)
    elif a.cmd == "bot":
        rodar_bot(a.horas)
    elif a.cmd == "mensagens":
        print(f"{processar_mensagens()} mensagem(ns) respondida(s).")
    elif a.cmd == "chatid":
        mostrar_chat_id()


if __name__ == "__main__":
    main()
