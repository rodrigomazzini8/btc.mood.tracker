# -*- coding: utf-8 -*-
"""
Configuração comum dos testes.

Os módulos do projeto moram em `scripts/` e são importados pelo nome
(`import cycle_model`), como o `dashboard.py` faz. Aqui garantimos que essa
pasta esteja no `sys.path` quando o pytest roda da raiz do repositório.

REGRA DESTA SUÍTE: **nenhum teste acessa a rede.** Tudo roda com séries
sintéticas ou fixtures em memória, para o CI ser rápido e determinístico.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "scripts"))


@pytest.fixture(scope="session")
def preco_sintetico() -> pd.DataFrame:
    """
    Preço fake com ciclos de ~4 anos, tendência de alta e ruído — o mesmo
    gerador que o autoteste do `cycle_model` usa.
    """
    import cycle_model as cm
    return cm._preco_sintetico(anos=9, semente=7)


@pytest.fixture(scope="session")
def preco_rampa() -> pd.DataFrame:
    """Preço que só sobe: útil para checar extremos (topo = score alto)."""
    datas = pd.date_range("2016-01-01", periods=2500, freq="D")
    return pd.DataFrame({"date": datas,
                         "price": np.exp(np.linspace(np.log(500), np.log(90000), 2500))})
