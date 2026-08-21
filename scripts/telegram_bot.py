# -*- coding: utf-8 -*-
"""
telegram_bot.py — o BTC Cycle Model no Telegram.

Sem biblioteca de bot: só `requests`, que o projeto já usa. São duas formas
de uso, e elas se complementam:

  1. **Interativo** (`python scripts/10_telegram.py --polling`): o bot fica
     ouvindo e responde a comandos — /score, /termometro, /semanal,
     /rebalancear... Roda no seu computador, num Raspberry, onde quiser.
  2. **Alerta diário sem servidor** (`--alerta`): calcula a fase do ciclo e
     só manda mensagem quando ela MUDA. Pensado para rodar num cron ou no
     GitHub Actions — não precisa de máquina ligada.

Configuração (nunca comite token):

    export TELEGRAM_BOT_TOKEN="123456:ABC..."     # do @BotFather
    export TELEGRAM_CHAT_ID="123456789"           # /alerta responde com o seu
    export TELEGRAM_CHAT_IDS="111,222"            # opcional: só estes falam com o bot

Nada aqui é recomendação financeira.
"""

from __future__ import annotations

import os
import re
import json
import time
import html
import datetime as dt

import pandas as pd

import common
import coinmetrics
import cycle_model as cm
import termometro as term

API = "https://api.telegram.org/bot{token}/{metodo}"
HTTP_TIMEOUT = 30

TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
CHAT_ENV = "TELEGRAM_CHAT_ID"
PERMITIDOS_ENV = "TELEGRAM_CHAT_IDS"

_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSINANTES = os.path.join(_RAIZ, "cache", "telegram_assinantes.json")

AVISO = "Conteúdo educativo — não é recomendação financeira."


# ==========================================================================
# 1) Camada da API do Telegram
# ==========================================================================

def token_configurado() -> str:
    """Token do bot (variável de ambiente). String vazia se não houver."""
    return os.environ.get(TOKEN_ENV, "").strip()


def chats_permitidos() -> set[str]:
    """
    Allowlist opcional de chats. Vazia = responde a qualquer um (o bot só
    devolve dado público de mercado, então isso é seguro por padrão).
    """
    bruto = os.environ.get(PERMITIDOS_ENV, "")
    return {c.strip() for c in bruto.split(",") if c.strip()}


def _chamar(metodo: str, dados: dict, token: str | None = None,
            timeout: int = HTTP_TIMEOUT) -> dict:
    """
    Chama um método da API do Telegram. Nunca levanta: devolve
    {'ok': False, 'erro': ...} quando algo dá errado, porque um bot que morre
    numa falha de rede é pior que um bot que erra uma mensagem.
    """
    tok = token or token_configurado()
    if not tok:
        return {"ok": False, "erro": f"defina {TOKEN_ENV}"}
    try:
        import requests
        r = requests.post(API.format(token=tok, metodo=metodo), json=dados,
                          timeout=timeout)
        corpo = r.json()
        if not corpo.get("ok"):
            return {"ok": False, "erro": corpo.get("description", "erro")}
        return corpo
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def enviar(chat_id, texto: str, token: str | None = None) -> bool:
    """Manda uma mensagem (HTML). True se o Telegram aceitou."""
    resp = _chamar("sendMessage", {
        "chat_id": chat_id, "text": texto, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, token=token)
    if not resp.get("ok"):
        print(f"[telegram] falha ao enviar para {chat_id}: {resp.get('erro')}")
    return bool(resp.get("ok"))


def buscar_updates(offset: int = 0, espera: int = 25,
                   token: str | None = None) -> list[dict]:
    """
    Long polling: a chamada fica aberta até `espera` segundos aguardando
    mensagem. É o jeito de ouvir sem webhook e sem servidor exposto.
    """
    resp = _chamar("getUpdates", {"offset": offset, "timeout": espera},
                   token=token, timeout=espera + 10)
    return resp.get("result", []) if resp.get("ok") else []


# ==========================================================================
# 2) Assinantes do alerta (quem recebe quando a fase muda)
# ==========================================================================

def ler_assinantes(caminho: str | None = None) -> list[str]:
    """Chats inscritos no alerta de mudança de fase."""
    try:
        with open(caminho or ASSINANTES, "r", encoding="utf-8") as f:
            return [str(c) for c in json.load(f)]
    except Exception:
        return []


def salvar_assinantes(chats, caminho: str | None = None) -> None:
    destino = caminho or ASSINANTES
    try:
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(sorted({str(c) for c in chats}), f)
    except Exception as e:
        print(f"[telegram] não consegui salvar assinantes: {e}")


def assinar(chat_id, caminho: str | None = None) -> bool:
    """Inscreve um chat. False se já estava inscrito."""
    atuais = ler_assinantes(caminho)
    if str(chat_id) in atuais:
        return False
    salvar_assinantes(atuais + [str(chat_id)], caminho)
    return True


def desassinar(chat_id, caminho: str | None = None) -> bool:
    """Remove um chat. False se não estava inscrito."""
    atuais = ler_assinantes(caminho)
    if str(chat_id) not in atuais:
        return False
    salvar_assinantes([c for c in atuais if c != str(chat_id)], caminho)
    return True


# ==========================================================================
# 3) Dados — buscados uma vez e reaproveitados entre mensagens
# ==========================================================================

class Contexto:
    """
    Guarda preço/on-chain por alguns minutos.

    Sem isso, cada `/score` refaria download e recálculo — lento para quem
    manda a mensagem e desnecessário, já que o modelo é de CICLO: o número
    não muda de forma relevante em 15 minutos.
    """

    def __init__(self, ttl_segundos: int = 900):
        self.ttl = ttl_segundos
        self._em = 0.0
        self._dados = None

    def _carregar(self) -> dict:
        dados_cm = coinmetrics.fetch_coinmetrics()
        preco = common.fetch_btc_price(dias=2200)
        if preco.empty and dados_cm is not None and not dados_cm.empty:
            # Corretora bloqueada? O dataset on-chain também traz preço.
            preco = dados_cm[["date", "price"]].tail(2200).reset_index(drop=True)
        if preco.empty:
            raise RuntimeError("sem preço nem on-chain agora")

        fng = common.fetch_fear_greed(limit=0)
        oc = cm.buscar_series_onchain() if cm.tem_onchain() else {}
        snap = cm.calcular(preco, fng=fng if not fng.empty else None,
                           series_onchain=oc, dados_cm=dados_cm)
        hist = cm.serie_score(preco, fng if not fng.empty else None, oc,
                              dados_cm=dados_cm)
        return {"preco": preco, "fng": fng, "cm": dados_cm, "oc": oc,
                "snap": snap, "hist": hist,
                "semanal": cm.serie_semanal(hist)}

    def dados(self, forcar: bool = False) -> dict:
        if forcar or self._dados is None or (time.time() - self._em) > self.ttl:
            self._dados = self._carregar()
            self._em = time.time()
        return self._dados


# ==========================================================================
# 4) Mensagens
# ==========================================================================

def _e(texto) -> str:
    """Escapa para o parse_mode HTML do Telegram."""
    return html.escape(str(texto), quote=False)


AJUDA = (
    "<b>BTC Cycle Model</b>\n"
    "Em que ponto do ciclo o Bitcoin está — e o que isso significa para a "
    "posição.\n\n"
    "/score — score de ciclo (0–100), fase e plano\n"
    "/termometro — o sinal consolidado (−2 a +2)\n"
    "/preco — preço, variação e Fear &amp; Greed\n"
    "/semanal — as últimas semanas (o modelo é de ciclo)\n"
    "/rebalancear <i>total em_btc</i> — ex.: <code>/rebalancear 10000 4000</code>\n"
    "/alertas on|off — avisar quando a fase mudar\n"
    "/ajuda — esta mensagem\n\n"
    f"<i>{AVISO}</i>"
)


def msg_score(dados: dict) -> str:
    snap = dados["snap"]
    plano = snap["plano"]
    linhas = [
        f"<b>{_e(snap['fase'])}</b> · score <b>{snap['score']:.0f}</b>/100",
        f"BTC <b>${snap['preco']:,.0f}</b>",
        "",
        f"<b>{_e(plano['acao'])}</b>",
        _e(plano["detalhe"]),
        "",
        f"Exposição-alvo: <b>{plano['exposicao_alvo']:.0f}%</b> em BTC · "
        f"DCA <b>{plano['dca']:.2f}×</b>",
        "",
        "<b>Composição</b>",
    ]
    for c in snap["componentes"]:
        if not c["ok"]:
            continue
        selo = "" if c["tipo"] == "on-chain" else f" <i>({_e(c['tipo'])})</i>"
        linhas.append(f"· {_e(c['nome'])}: {c['sub_score']:.0f} — "
                      f"{_e(c['rotulo'].lower())}{selo}")

    if snap.get("fontes_defasadas"):
        atrasos = ", ".join(
            f"{cm.NOMES_FONTES.get(d['fonte'], d['fonte'])} ({d['idade']}d)"
            for d in snap["fontes_defasadas"])
        linhas += ["", f"⚠️ On-chain atrasado: {_e(atrasos)}. "
                       "Os pilares afetados usam proxies de preço."]

    delta = snap.get("delta30")
    if delta is not None and delta == delta:
        linhas += ["", f"Momentum: {delta:+.0f} pontos em 30 dias"]
    linhas += ["", f"<i>{AVISO}</i>"]
    return "\n".join(linhas)


def msg_termometro(dados: dict) -> str:
    snap = term.montar_snapshot(dados["preco"],
                                fng_atual=_ultimo_fng(dados["fng"]))
    consolidado = term.consolidar(snap)
    sinal = term.score_para_sinal(consolidado)
    ok = snap[snap["ok"]]
    compra = int((ok["score"] > 0).sum())
    neutro = int((ok["score"] == 0).sum())
    venda = int((ok["score"] < 0).sum())
    linhas = [f"<b>{_e(sinal)}</b> · score <b>{consolidado:+.2f}</b> "
              f"(−2 a +2)",
              f"{len(ok)} indicadores · 🟢 {compra} · ⚪ {neutro} · 🔴 {venda}",
              ""]
    for r in ok.itertuples():
        valor = f"{r.valor:.2f}" if abs(r.valor) < 1000 else f"{r.valor:,.0f}"
        linhas.append(f"· {_e(r.indicador)}: {valor} — {_e(r.sinal.lower())}")
    linhas += ["", f"<i>{AVISO}</i>"]
    return "\n".join(linhas)


def msg_preco(dados: dict) -> str:
    preco = dados["preco"]
    atual = float(preco["price"].iloc[-1])
    var = ((atual / float(preco["price"].iloc[-2]) - 1) * 100
           if len(preco) > 1 else 0.0)
    seta = "🟢" if var >= 0 else "🔴"
    linhas = [f"<b>BTC ${atual:,.0f}</b>  {seta} {var:+.2f}%",
              f"<i>fechamento de {preco['date'].iloc[-1].date()}</i>"]
    fng = _ultimo_fng(dados["fng"])
    if fng == fng:
        clima = ("ganância" if fng >= 55 else "medo" if fng <= 45 else "neutro")
        linhas.append(f"Fear &amp; Greed: <b>{fng:.0f}</b>/100 ({clima})")
    return "\n".join(linhas)


def msg_semanal(dados: dict, semanas: int = 6) -> str:
    sem = dados["semanal"]
    if sem.empty:
        return "Ainda não tenho histórico suficiente para o score semanal."
    linhas = ["<b>Score semanal</b> — uma decisão por semana, no máximo", ""]
    for r in sem.tail(semanas).iloc[::-1].itertuples():
        linhas.append(f"<code>{r.date.date()}</code>  ${r.price:>9,.0f}  "
                      f"{r.score:5.1f}  {_e(r.fase)}")
    linhas += ["", f"<i>{AVISO}</i>"]
    return "\n".join(linhas)


def msg_rebalanceamento(dados: dict, argumentos: str) -> str:
    """`/rebalancear <patrimônio> <quanto já está em BTC>`."""
    numeros = re.findall(r"-?\d+[.,]?\d*", argumentos or "")
    if len(numeros) < 2:
        return ("Use assim: <code>/rebalancear 10000 4000</code>\n"
                "(patrimônio total considerado e quanto disso já está em BTC)")
    try:
        total = float(numeros[0].replace(",", "."))
        em_btc = float(numeros[1].replace(",", "."))
    except ValueError:
        return "Não entendi os números. Ex.: <code>/rebalancear 10000 4000</code>"

    snap = dados["snap"]
    reb = cm.plano_rebalanceamento(snap["score"], patrimonio=total,
                                   valor_em_btc=em_btc,
                                   preco_btc=snap.get("preco"),
                                   aporte_base=0.0)
    if reb["acao"] == "—":
        return "Valores inválidos: o patrimônio precisa ser maior que zero."

    linhas = [f"<b>{_e(reb['acao'])}</b> · fase {_e(reb['fase'])}",
              f"Você: <b>{reb['atual_pct']:.0f}%</b> em BTC · "
              f"alvo: <b>{reb['alvo_pct']:.0f}%</b>",
              ""]
    if reb["acao"] != "MANTER":
        linha = f"Ajuste: <b>{reb['ajuste']:+,.0f}</b>"
        if reb["ajuste_btc"] is not None:
            linha += f" ({reb['ajuste_btc']:+.4f} BTC)"
        linhas.append(linha)
    linhas += [_e(reb["detalhe"]), "", f"<i>{AVISO}</i>"]
    return "\n".join(linhas)


def _ultimo_fng(fng: pd.DataFrame) -> float:
    if fng is None or len(fng) == 0 or "fng" not in getattr(fng, "columns", []):
        return float("nan")
    serie = fng["fng"].dropna()
    return float(serie.iloc[-1]) if not serie.empty else float("nan")


def msg_alerta(fase_nova: str, fase_antes: str | None, dados: dict) -> str:
    """Mensagem de mudança de fase (a que o alerta diário dispara)."""
    snap = dados["snap"]
    plano = snap["plano"]
    cabeca = (f"🔔 <b>Mudou de fase: {_e(fase_antes)} → {_e(fase_nova)}</b>"
              if fase_antes else f"🔔 <b>{_e(fase_nova)}</b>")
    return "\n".join([
        cabeca,
        f"Score de ciclo: <b>{snap['score']:.0f}</b>/100 · "
        f"BTC ${snap['preco']:,.0f}",
        "",
        f"<b>{_e(plano['acao'])}</b>",
        _e(cm.DESCRICAO_FASE.get(fase_nova, "")),
        "",
        f"Exposição-alvo <b>{plano['exposicao_alvo']:.0f}%</b> · "
        f"DCA <b>{plano['dca']:.2f}×</b>",
        "",
        f"<i>{AVISO}</i>",
    ])


# ==========================================================================
# 5) Roteador de comandos (função pura — é o que os testes exercitam)
# ==========================================================================

def responder(texto: str, contexto: Contexto, chat_id=None,
              arquivo_assinantes: str | None = None) -> str | None:
    """
    Resposta para uma mensagem recebida. `None` = ficar calado (mensagem que
    não é comando; em grupo, o bot não deve responder a toda conversa).
    """
    texto = (texto or "").strip()
    if not texto.startswith("/"):
        return None

    partes = texto.split(maxsplit=1)
    comando = partes[0].lower()
    argumentos = partes[1] if len(partes) > 1 else ""
    if "@" in comando:                    # /score@MeuBot, em grupos
        comando = comando.split("@", 1)[0]

    if comando in ("/start", "/ajuda", "/help"):
        return AJUDA

    if comando == "/alertas":
        opcao = argumentos.strip().lower()
        if opcao in ("on", "ligar", "sim"):
            novo = assinar(chat_id, arquivo_assinantes)
            return ("Pronto: aviso aqui quando a fase do ciclo mudar."
                    if novo else "Este chat já recebe os alertas.")
        if opcao in ("off", "desligar", "nao", "não"):
            saiu = desassinar(chat_id, arquivo_assinantes)
            return ("Alertas desligados." if saiu
                    else "Este chat não estava recebendo alertas.")
        inscrito = str(chat_id) in ler_assinantes(arquivo_assinantes)
        return (f"Alertas: <b>{'ligados' if inscrito else 'desligados'}</b>\n"
                "Use <code>/alertas on</code> ou <code>/alertas off</code>.")

    try:
        dados = contexto.dados()
    except Exception as e:
        return f"Não consegui buscar os dados agora ({_e(e)}). Tente daqui a pouco."

    if comando == "/score":
        return msg_score(dados)
    if comando in ("/termometro", "/termômetro"):
        return msg_termometro(dados)
    if comando in ("/preco", "/preço"):
        return msg_preco(dados)
    if comando == "/semanal":
        return msg_semanal(dados)
    if comando in ("/rebalancear", "/rebalance"):
        return msg_rebalanceamento(dados, argumentos)

    return "Não conheço esse comando.\n\n" + AJUDA


# ==========================================================================
# 6) Loop de polling e envio do alerta
# ==========================================================================

def rodar_polling(contexto: Contexto | None = None, token: str | None = None,
                  max_ciclos: int | None = None) -> int:
    """
    Fica ouvindo e respondendo. `max_ciclos` limita as voltas (testes).

    Uma falha ao tratar UMA mensagem não pode derrubar o bot — por isso cada
    update é tratado dentro de try/except.
    """
    tok = token or token_configurado()
    if not tok:
        print(f"[telegram] defina {TOKEN_ENV} (fale com o @BotFather).")
        return 1

    contexto = contexto or Contexto()
    permitidos = chats_permitidos()
    print("[telegram] ouvindo... (Ctrl+C para parar)"
          + (f" allowlist: {sorted(permitidos)}" if permitidos else ""))

    offset, voltas = 0, 0
    while max_ciclos is None or voltas < max_ciclos:
        voltas += 1
        for update in buscar_updates(offset=offset, token=tok):
            offset = max(offset, int(update.get("update_id", 0)) + 1)
            try:
                msg = update.get("message") or update.get("edited_message") or {}
                chat_id = str((msg.get("chat") or {}).get("id", ""))
                texto = msg.get("text", "")
                if not chat_id or not texto:
                    continue
                if permitidos and chat_id not in permitidos:
                    print(f"[telegram] chat {chat_id} fora da allowlist")
                    continue
                resposta = responder(texto, contexto, chat_id=chat_id)
                if resposta:
                    enviar(chat_id, resposta, token=tok)
                    print(f"[telegram] {texto.split()[0]} -> chat {chat_id}")
            except Exception as e:                      # nunca derruba o bot
                print(f"[telegram] erro tratando update: {e}")
    return 0


def enviar_para_todos(texto: str, chats=None, token: str | None = None) -> int:
    """Manda a mesma mensagem para os assinantes (+ TELEGRAM_CHAT_ID)."""
    destinos = set(str(c) for c in (chats if chats is not None
                                    else ler_assinantes()))
    fixo = os.environ.get(CHAT_ENV, "").strip()
    if fixo:
        destinos.add(fixo)
    if not destinos:
        print("[telegram] ninguém inscrito e sem TELEGRAM_CHAT_ID — nada enviado.")
        return 0
    return sum(1 for chat in sorted(destinos) if enviar(chat, texto, token=token))


def agora_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")
