# -*- coding: utf-8 -*-
"""
08_alerta.py
============

Avisa quando o **ciclo muda de fase** — e só nessa hora.

O Cycle Model é um modelo de ciclo: olhar o score todo dia é ruído e vira
ansiedade. O que muda decisão é a virada de fase (ACUMULAÇÃO -> EXPANSÃO ->
DISTRIBUIÇÃO ...). Este script roda quantas vezes você quiser (um cron
diário, por exemplo) e **fica calado** enquanto nada muda.

Detalhes que evitam alerta falso:
  - usa o score do **fechamento semanal**, não o do dia;
  - exige uma **margem** para confirmar a fase nova (histerese), senão um
    score oscilando em torno de 35 dispararia um alerta por dia;
  - guarda a última fase em `cache/alerta_fase.json`.

Rodar:
    python scripts/08_alerta.py                  # avisa só se mudou de fase
    python scripts/08_alerta.py --forcar         # imprime a situação de qualquer jeito
    python scripts/08_alerta.py --seco           # não grava o estado (teste)
    python scripts/08_alerta.py --margem 2.5     # exige mais margem para trocar de fase

Enviar para algum lugar (opcional):
    export ALERTA_WEBHOOK="https://..."          # recebe um POST JSON
    python scripts/08_alerta.py

    # Telegram: a URL do sendMessage do seu bot funciona direto
    export ALERTA_WEBHOOK="https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>"

Saída: código 0 sempre que rodar bem (com ou sem alerta). Nada aqui é
recomendação financeira.
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common          # noqa: E402
import cycle_model as cm  # noqa: E402

WEBHOOK_ENV = "ALERTA_WEBHOOK"

# A leitura/gravação da fase mora no cycle_model, para o bot do Telegram
# (scripts/10_telegram.py) usar exatamente a mesma.
ler_estado = cm.ler_estado_fase
salvar_estado = cm.salvar_estado_fase


def montar_mensagem(fase_nova: str, fase_antes: str | None, score: float,
                    preco: float, res: dict) -> str:
    """Texto curto, pronto para mandar no Telegram/e-mail."""
    plano = res["plano"]
    if fase_antes:
        cabeca = f"🔔 BTC mudou de fase: {fase_antes} → {fase_nova}"
    else:
        cabeca = f"🔔 BTC Cycle Model: {fase_nova}"
    return (
        f"{cabeca}\n"
        f"Score de ciclo: {score:.0f}/100 · BTC ${preco:,.0f}\n"
        f"Ação: {plano['acao']}\n"
        f"Exposição-alvo: {plano['exposicao_alvo']:.0f}% em BTC "
        f"(realizado {plano['realizado_alvo']:.0f}%)\n"
        f"DCA: {plano['dca']:.2f}× o aporte normal\n"
        f"{cm.DESCRICAO_FASE.get(fase_nova, '')}\n"
        f"— não é recomendação financeira"
    )


def enviar_webhook(texto: str, url: str) -> bool:
    """
    Manda o texto para um webhook. O corpo tem `text` (formato do Telegram)
    e `message`, então serve para os dois casos mais comuns sem configuração.
    """
    try:
        import requests
        r = requests.post(url, json={"text": texto, "message": texto},
                          timeout=common.HTTP_TIMEOUT)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[alerta] webhook falhou: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Alerta de mudança de fase do ciclo")
    ap.add_argument("--forcar", action="store_true",
                    help="imprime a situação mesmo sem mudança de fase")
    ap.add_argument("--seco", action="store_true",
                    help="não grava o estado (útil para testar)")
    ap.add_argument("--margem", type=float, default=1.5,
                    help="pontos de score exigidos para confirmar a fase nova")
    ap.add_argument("--webhook", default=os.environ.get(WEBHOOK_ENV, ""),
                    help=f"URL para POST do alerta (ou variável {WEBHOOK_ENV})")
    args = ap.parse_args()

    # --- dados: on-chain grátis + preço (com fallback no próprio dataset) ---
    dados_cm = cm.fetch_coinmetrics()
    preco = common.fetch_btc_price(dias=2200)
    if preco.empty and not dados_cm.empty:
        preco = dados_cm[["date", "price"]].tail(2200).reset_index(drop=True)
    if preco.empty:
        print("[alerta] sem preço nem dados on-chain agora — nada a fazer.")
        return 0

    fng = common.fetch_fear_greed(limit=0)
    series_oc = cm.buscar_series_onchain() if cm.tem_onchain() else {}

    res = cm.calcular(preco, fng=fng if not fng.empty else None,
                      series_onchain=series_oc, dados_cm=dados_cm)
    hist = cm.serie_score(preco, fng if not fng.empty else None, series_oc,
                          dados_cm=dados_cm)
    semanal = cm.serie_semanal(hist)
    if semanal.empty:
        print("[alerta] histórico insuficiente para o score semanal.")
        return 0

    score_semana = float(semanal["score"].iloc[-1])
    estado = ler_estado()
    fase_antes = estado.get("fase")
    fase_agora = cm.fase_confirmada(score_semana, fase_antes, margem=args.margem)
    mudou = fase_agora != fase_antes

    if not mudou and not args.forcar:
        print(f"[alerta] sem mudança de fase ({fase_agora}, score semanal "
              f"{score_semana:.1f}). Nada enviado.")
        return 0

    texto = montar_mensagem(fase_agora, fase_antes if mudou else None,
                            score_semana, res["preco"], res)
    print()
    print(texto)
    print()

    if args.webhook:
        print("[alerta] enviado ao webhook."
              if enviar_webhook(texto, args.webhook) else
              "[alerta] não foi possível enviar ao webhook.")

    if not args.seco:
        salvar_estado(fase_agora, score_semana)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
