# -*- coding: utf-8 -*-
"""
09_atualiza_dados.py
====================

Mantém o snapshot on-chain do repositório (`data/onchain_btc.csv`) **em dia**.

Por que existe: o dashboard precisa de MVRV/NUPL/Puell atuais, mas as fontes
públicas nem sempre estão acessíveis de onde o app roda (datacenter, firewall,
tier grátis com rede restrita) — e o CSV que a Coin Metrics publicava no
GitHub **parou em 24/05/2026**. A solução é buscar o dado onde a rede é
aberta (o runner do GitHub Actions), versionar um arquivo pequeno no
repositório e deixar o app ler dali.

Roda todo dia pelo `.github/workflows/dados.yml`. Também dá para rodar na mão:

    python scripts/09_atualiza_dados.py             # atualiza se precisar
    python scripts/09_atualiza_dados.py --forcar    # reescreve mesmo sem mudança
    python scripts/09_atualiza_dados.py --max-atraso 3

Saída: código 0 se o snapshot ficou dentro do atraso aceitável; 1 se a fonte
está velha ou não respondeu (aí o CI acusa em vez de commitar dado ruim).
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coinmetrics  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Atualiza o snapshot on-chain")
    ap.add_argument("--max-atraso", type=int, default=2,
                    help="atraso máximo aceitável, em dias (padrão: 2)")
    ap.add_argument("--forcar", action="store_true",
                    help="grava mesmo que nada tenha mudado")
    ap.add_argument("--destino", default=None,
                    help="caminho do snapshot (padrão: data/onchain_btc.csv)")
    args = ap.parse_args()

    print(">> [09] Atualizando o snapshot on-chain do repositório\n")

    destino = args.destino or coinmetrics.SNAPSHOT
    antes = coinmetrics.ler_snapshot(destino)
    if not antes.empty:
        print(f"   Snapshot atual: {len(antes)} dias, até "
              f"{antes['date'].max().date()} "
              f"({coinmetrics.atraso_em_dias(antes)}d de atraso)")
    else:
        print("   Snapshot atual: não existe ainda")

    # Sem cache: aqui o objetivo é justamente ir na fonte.
    novo = coinmetrics.fetch_api()
    origem = "api"
    if novo.empty:
        print("   API community não respondeu — tentando o mirror histórico")
        novo = coinmetrics.fetch_mirror()
        origem = "mirror"

    if novo.empty:
        print("\n   ✖ Nenhuma fonte respondeu. Snapshot mantido como estava.")
        return 1

    atraso = coinmetrics.atraso_em_dias(novo)
    print(f"   Fonte '{origem}': {len(novo)} dias, até {novo['date'].max().date()} "
          f"({atraso}d de atraso)")

    # --- sanidade antes de gravar (não commitar lixo) ---
    problemas = []
    if len(novo) < 1000:
        problemas.append(f"série curta demais ({len(novo)} dias)")
    if not (novo["PriceUSD"] > 0).all():
        problemas.append("preço zerado ou negativo")
    if not novo["date"].is_monotonic_increasing:
        problemas.append("datas fora de ordem")
    if not antes.empty and novo["date"].max() < antes["date"].max():
        problemas.append("dado novo é mais VELHO que o snapshot atual")
    if problemas:
        print("\n   ✖ Dado suspeito, snapshot preservado: " + "; ".join(problemas))
        return 1

    mudou = (antes.empty
             or novo["date"].max() > antes["date"].max()
             or len(novo) != len(antes))
    if not mudou and not args.forcar:
        if atraso <= args.max_atraso:
            print("\n   Nada novo — o snapshot já está em dia.")
            return 0
        print(f"\n   ✖ Nada novo E a fonte está {atraso}d atrasada "
              f"(máximo {args.max_atraso}). O snapshot ficou velho.")
        return 1

    coinmetrics.salvar_snapshot(novo, destino)
    tamanho = os.path.getsize(destino) / 1024
    print(f"\n   ✔ Snapshot gravado: {destino} ({tamanho:.0f} KB)")

    # Confere o que ficou no disco (não confiar no que se acabou de escrever).
    relido = coinmetrics.ler_snapshot(destino)
    print(f"   Relido: {len(relido)} dias, até {relido['date'].max().date()}")
    derivado = coinmetrics._derivar(relido)
    ultima = derivado.dropna(subset=["cm_mvrv_z"]).iloc[-1]
    print(f"   Última leitura: preço ${ultima['price']:,.0f} · "
          f"MVRV {ultima['cm_mvrv']:.2f} · Z {ultima['cm_mvrv_z']:.2f} · "
          f"NUPL {ultima['cm_nupl']:.2f}")

    if atraso > args.max_atraso:
        print(f"\n   ✖ Fonte com {atraso}d de atraso (máximo {args.max_atraso}).")
        return 1
    print("\n>> Concluído.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
