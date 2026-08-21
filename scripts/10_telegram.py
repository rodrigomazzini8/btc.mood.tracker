# -*- coding: utf-8 -*-
"""
10_telegram.py
==============

O bot do Telegram do projeto, em três modos:

    python scripts/10_telegram.py --polling
        Fica ouvindo e responde aos comandos (/score, /termometro, /semanal,
        /rebalancear, /alertas...). Roda no seu computador; Ctrl+C encerra.

    python scripts/10_telegram.py --alerta
        Calcula a fase do ciclo e **só manda mensagem se ela mudou**. Feito
        para cron ou GitHub Actions — não precisa de máquina ligada. O estado
        fica em `data/alerta_estado.json` para o CI lembrar entre execuções.

    python scripts/10_telegram.py --teste
        Imprime no terminal as mensagens que o bot mandaria. Não envia nada e
        nem precisa de token — serve para conferir o texto.

Configuração (nunca comite token):

    export TELEGRAM_BOT_TOKEN="123456:ABC..."   # do @BotFather
    export TELEGRAM_CHAT_ID="123456789"         # destino do alerta
    export TELEGRAM_CHAT_IDS="111,222"          # opcional: allowlist do bot

Como criar o bot: fale com o **@BotFather** no Telegram, mande /newbot, siga
as instruções e guarde o token. Depois mande /start para o seu bot e use
/alertas on para receber os avisos de mudança de fase.

Nada aqui é recomendação financeira.
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cycle_model as cm       # noqa: E402
import telegram_bot as tg      # noqa: E402


def modo_teste(contexto: tg.Contexto) -> int:
    """Imprime as mensagens sem enviar nada (não exige token)."""
    print(">> [10] Modo teste: mensagens que o bot mandaria\n")
    try:
        dados = contexto.dados()
    except Exception as e:
        print(f"Não consegui montar os dados: {e}")
        return 1

    for titulo, texto in (
        ("/ajuda", tg.AJUDA),
        ("/preco", tg.msg_preco(dados)),
        ("/score", tg.msg_score(dados)),
        ("/termometro", tg.msg_termometro(dados)),
        ("/semanal", tg.msg_semanal(dados)),
        ("/rebalancear 10000 4000", tg.msg_rebalanceamento(dados, "10000 4000")),
    ):
        print("=" * 62)
        print(f"### {titulo}")
        print("=" * 62)
        print(texto.replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", "")
              .replace("<code>", "").replace("</code>", "")
              .replace("&amp;", "&"))
        print()
    return 0


def modo_alerta(contexto: tg.Contexto, estado_arquivo: str, margem: float,
                forcar: bool, seco: bool) -> int:
    """Manda mensagem só quando a fase do ciclo muda."""
    print(">> [10] Alerta de mudança de fase\n")
    try:
        dados = contexto.dados()
    except Exception as e:
        print(f"Não consegui montar os dados: {e}")
        return 1

    semanal = dados["semanal"]
    if semanal.empty:
        print("Histórico insuficiente para o score semanal.")
        return 1

    # O modelo é de ciclo: a decisão sai do FECHAMENTO SEMANAL, não do dia.
    score_semana = float(semanal["score"].iloc[-1])
    estado = cm.ler_estado_fase(estado_arquivo)
    fase_antes = estado.get("fase")
    fase_agora = cm.fase_confirmada(score_semana, fase_antes, margem=margem)
    mudou = fase_agora != fase_antes

    print(f"   Fase guardada: {fase_antes or '(nenhuma)'}")
    print(f"   Fase agora:    {fase_agora} (score semanal {score_semana:.1f})")

    if not mudou and not forcar:
        print("\n   Sem mudança de fase — nada enviado.")
        return 0

    texto = tg.msg_alerta(fase_agora, fase_antes if mudou else None, dados)
    if seco:
        print("\n--- mensagem (modo seco, não enviada) ---")
        print(texto)
        return 0

    enviados = tg.enviar_para_todos(texto)
    print(f"\n   Enviado para {enviados} chat(s).")
    if enviados or not tg.token_configurado():
        cm.salvar_estado_fase(fase_agora, score_semana, estado_arquivo,
                              extra={"preco": round(dados["snap"]["preco"], 2)})
        print(f"   Estado gravado em {estado_arquivo}")
    else:
        # Falhou o envio: NÃO gravar, senão o alerta desta virada some.
        print("   Envio falhou — estado preservado para tentar de novo.")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Bot do Telegram do BTC Cycle Model")
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--polling", action="store_true",
                      help="fica ouvindo e responde aos comandos")
    modo.add_argument("--alerta", action="store_true",
                      help="envia só se a fase do ciclo mudou (cron/CI)")
    modo.add_argument("--teste", action="store_true",
                      help="imprime as mensagens sem enviar nada")
    ap.add_argument("--estado", default=cm.ESTADO_FASE_VERSIONADO,
                    help="arquivo com a última fase avisada")
    ap.add_argument("--margem", type=float, default=1.5,
                    help="pontos de score exigidos para confirmar a fase nova")
    ap.add_argument("--forcar", action="store_true",
                    help="envia mesmo sem mudança de fase")
    ap.add_argument("--seco", action="store_true",
                    help="mostra a mensagem do alerta sem enviar nem gravar")
    args = ap.parse_args()

    contexto = tg.Contexto()
    if args.teste:
        return modo_teste(contexto)
    if args.alerta:
        return modo_alerta(contexto, args.estado, args.margem,
                           args.forcar, args.seco)
    return tg.rodar_polling(contexto)      # padrão: modo interativo


if __name__ == "__main__":
    raise SystemExit(main())
