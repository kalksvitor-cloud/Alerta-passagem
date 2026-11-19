"""API do site (função serverless da Vercel).

GET  /api/app?acao=buscar&origem=CNF&destino=CWB&data=2027-01-15[&volta=...]
     data pode ser faixa: 2027-01-10:2027-01-20
GET  /api/app?acao=alertas
POST /api/app?acao=criar     {"origem","destino","datas","preco_max","volta"}
POST /api/app?acao=remover   {"id"}
Toda chamada precisa do cabeçalho X-Senha igual à variável SITE_SENHA (se ela existir).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import passagens as p  # noqa: E402


def buscar(q):
    origem, destino = p.aeroporto(q["origem"]), p.aeroporto(q["destino"])
    datas = p.expandir_datas(p._data(q["data"]))
    volta = p._data(q["volta"]) if q.get("volta") else None
    todos = p.buscar_datas(origem, destino, datas, volta)
    if not todos:
        return {"origem": origem, "destino": destino, "dias": [], "melhor": None}
    dias = [{"data": d, "preco": r["voos"][0]["preco"]} for d, r in sorted(todos.items())]
    melhor_data = min(todos, key=lambda d: todos[d]["voos"][0]["preco"])
    r = todos[melhor_data]
    links = p.links_outros_sites(origem, destino, melhor_data, volta)
    links["Google Flights"] = r["link"]
    return {
        "origem": origem, "destino": destino, "volta": volta, "dias": dias,
        "melhor": {"data": melhor_data, "voos": r["voos"][:15], "insight": r["insight"],
                   "fonte": r["fonte"], "links": links},
    }


def alertas():
    hist = p.carregar_historico()
    saida = []
    for a in p.carregar_alertas():
        regs = (hist.get(str(a["id"])) or {}).get("registros", [])
        saida.append({**a, "descricao": p.descrever_alerta(a),
                      "historico": regs[-60:],
                      "ultimo": regs[-1] if regs else None,
                      "minimo": min((r["preco"] for r in regs), default=None)})
    return saida


class handler(BaseHTTPRequestHandler):
    def _responder(self, codigo, dados):
        corpo = json.dumps(dados, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _rodar(self, corpo=None):
        senha = os.environ.get("SITE_SENHA", "").strip()
        if senha and self.headers.get("X-Senha", "") != senha:
            return self._responder(401, {"erro": "Senha errada"})
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        acao = q.get("acao")
        try:
            if acao == "buscar":
                return self._responder(200, buscar(q))
            if acao == "ofertas":
                volta = q.get("volta") or None
                lista = p.ofertas(p.aeroporto(q["origem"]), p.aeroporto(q["destino"]), q["data"], volta,
                                  q.get("booking_token"), q.get("departure_token"))
                return self._responder(200, {"ofertas": lista,
                                             "cias": p.links_cias(q["origem"], q["destino"], q["data"], volta)})
            if acao == "alertas":
                return self._responder(200, alertas())
            if acao == "criar":
                b = corpo or {}
                a = p.adicionar_alerta(b["origem"], b["destino"], p._data(b["datas"]),
                                       b.get("preco_max") or None,
                                       p._data(b["volta"]) if b.get("volta") else None)
                return self._responder(200, a)
            if acao == "remover":
                return self._responder(200, {"ok": p.remover_alerta(int((corpo or {})["id"]))})
            return self._responder(400, {"erro": "ação inválida"})
        except Exception as e:  # noqa: BLE001
            return self._responder(500, {"erro": str(e)})

    def do_GET(self):
        self._rodar()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._rodar(json.loads(self.rfile.read(n) or b"{}"))
