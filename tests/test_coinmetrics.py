# -*- coding: utf-8 -*-
"""
Testes do `coinmetrics.py` — a matemática das métricas derivadas.

Sem rede: montamos um CSV pequeno no formato exato do dataset da Coin
Metrics e conferimos MVRV, MVRV Z-Score, NUPL e Puell contra a conta feita
na mão.
"""

import io

import numpy as np
import pandas as pd
import pytest

import coinmetrics


def _csv_falso(dias: int = 800) -> io.StringIO:
    """CSV no formato da Coin Metrics, com números fáceis de conferir."""
    datas = pd.date_range("2020-01-01", periods=dias, freq="D")
    preco = np.linspace(10_000, 60_000, dias)
    oferta = 18_000_000 + np.arange(dias) * 900
    market_cap = preco * oferta
    mvrv = np.linspace(1.0, 3.0, dias)
    emissao = np.full(dias, 900.0) * preco
    df = pd.DataFrame({
        "time": datas, "PriceUSD": preco, "CapMrktCurUSD": market_cap,
        "CapMVRVCur": mvrv, "IssTotUSD": emissao,
    })
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return buf


@pytest.fixture(scope="module")
def derivadas() -> pd.DataFrame:
    return coinmetrics._cm_do_csv(_csv_falso())


def test_colunas_esperadas(derivadas):
    assert list(derivadas.columns) == [
        "date", "price", "cm_mvrv", "cm_mvrv_z", "cm_nupl", "cm_puell"]


def test_nupl_vem_do_mvrv(derivadas):
    # NUPL = 1 − 1/MVRV, por definição.
    esperado = 1 - 1 / derivadas["cm_mvrv"]
    assert np.allclose(derivadas["cm_nupl"], esperado)


def test_nupl_zero_quando_mvrv_um(derivadas):
    # MVRV = 1 significa mercado exatamente no custo médio: NUPL = 0.
    linha = derivadas.iloc[0]
    assert linha["cm_mvrv"] == pytest.approx(1.0)
    assert linha["cm_nupl"] == pytest.approx(0.0, abs=1e-9)


def test_mvrv_z_cresce_com_o_mvrv(derivadas):
    z = derivadas["cm_mvrv_z"].dropna()
    assert len(z) > 100
    assert z.iloc[-1] > z.iloc[0]


def test_mvrv_z_nao_olha_o_futuro(derivadas):
    """
    O z-score usa desvio-padrão EXPANDIDO (só o passado). Truncar a série no
    meio não pode mudar os valores já calculados até ali.
    """
    # Série mais longa aqui: o z-score só começa depois de 365 dias.
    longo = pd.read_csv(_csv_falso(1200))
    completo = coinmetrics._cm_do_csv(
        io.StringIO(longo.to_csv(index=False))).set_index("date")["cm_mvrv_z"]
    truncado = coinmetrics._cm_do_csv(
        io.StringIO(longo.iloc[:900].to_csv(index=False))
    ).set_index("date")["cm_mvrv_z"]
    comum = truncado.dropna().index
    assert len(comum) > 50
    assert np.allclose(completo.loc[comum], truncado.loc[comum])


def test_puell_medio_perto_de_um(derivadas):
    # Puell = emissão / média de 365d da emissão. Com emissão crescendo devagar,
    # ele fica um pouco acima de 1 — nunca negativo, nunca absurdo.
    puell = derivadas["cm_puell"].dropna()
    assert len(puell) > 100
    assert puell.between(0.5, 3.0).all()


def _sem_rede(monkeypatch, tmp_path):
    """Isola o módulo: sem rede, sem cache e sem snapshot no repositório."""
    monkeypatch.setattr(coinmetrics, "CACHE", str(tmp_path / "cache.csv"))
    monkeypatch.setattr(coinmetrics, "SNAPSHOT", str(tmp_path / "snap.csv"))

    def _explode(*a, **k):
        raise OSError("sem rede")

    monkeypatch.setattr("requests.get", _explode)


def test_falha_de_rede_nao_quebra(monkeypatch, tmp_path):
    """Sem rede, sem cache e sem snapshot: DataFrame vazio (não levanta)."""
    _sem_rede(monkeypatch, tmp_path)
    vazio = coinmetrics.fetch_coinmetrics()
    assert vazio.empty
    assert "cm_mvrv" in vazio.columns
    assert vazio.attrs["origem"] == "—"
    assert vazio.attrs["atraso"] == 999


# ------------------------------------------------------- escolha da fonte

def _bruto_ate(dias_atras: int, n: int = 500) -> pd.DataFrame:
    """Série crua terminando `dias_atras` dias antes de hoje."""
    fim = pd.Timestamp.today().normalize() - pd.Timedelta(days=dias_atras)
    datas = pd.date_range(end=fim, periods=n, freq="D")
    return pd.DataFrame({
        "date": datas, "PriceUSD": np.linspace(10_000, 60_000, n),
        "CapMrktCurUSD": np.linspace(2e11, 1.2e12, n),
        "CapMVRVCur": np.linspace(1.0, 3.0, n),
        "IssTotUSD": np.full(n, 3e7),
    })


def test_atraso_em_dias():
    assert coinmetrics.atraso_em_dias(_bruto_ate(0)) == 0
    assert coinmetrics.atraso_em_dias(_bruto_ate(5)) == 5
    assert coinmetrics.atraso_em_dias(pd.DataFrame()) == 999


def test_api_tem_prioridade(monkeypatch, tmp_path):
    _sem_rede(monkeypatch, tmp_path)
    monkeypatch.setattr(coinmetrics, "fetch_api", lambda *a, **k: _bruto_ate(1))
    monkeypatch.setattr(coinmetrics, "ler_snapshot", lambda *a, **k: _bruto_ate(0))
    bruto = coinmetrics.fetch_bruto(usar_cache=False)
    assert bruto.attrs["origem"] == "api"


def test_cai_no_snapshot_quando_a_api_falha(monkeypatch, tmp_path):
    _sem_rede(monkeypatch, tmp_path)
    vazio = pd.DataFrame(columns=coinmetrics.COLUNAS_BRUTAS)
    monkeypatch.setattr(coinmetrics, "fetch_api", lambda *a, **k: vazio)
    monkeypatch.setattr(coinmetrics, "ler_snapshot", lambda *a, **k: _bruto_ate(1))
    bruto = coinmetrics.fetch_bruto(usar_cache=False)
    assert bruto.attrs["origem"] == "snapshot"
    assert coinmetrics.atraso_em_dias(bruto) == 1


def test_fonte_velha_nao_encerra_a_busca(monkeypatch, tmp_path):
    """
    O caso real: o mirror do GitHub congelou. Uma fonte velha não pode
    interromper a procura por uma fresca — e, se ninguém estiver em dia,
    devolvemos a MENOS velha.
    """
    _sem_rede(monkeypatch, tmp_path)
    monkeypatch.setattr(coinmetrics, "fetch_api",
                        lambda *a, **k: pd.DataFrame(columns=coinmetrics.COLUNAS_BRUTAS))
    monkeypatch.setattr(coinmetrics, "ler_snapshot", lambda *a, **k: _bruto_ate(90))
    monkeypatch.setattr(coinmetrics, "fetch_mirror", lambda *a, **k: _bruto_ate(30))
    bruto = coinmetrics.fetch_bruto(usar_cache=False)
    assert coinmetrics.atraso_em_dias(bruto) == 30
    assert bruto.attrs["origem"] == "mirror"


def test_cache_velho_e_ignorado(monkeypatch, tmp_path):
    """Cache recém-escrito mas com dado velho não pode ser servido."""
    _sem_rede(monkeypatch, tmp_path)
    coinmetrics.salvar_snapshot(_bruto_ate(30), coinmetrics.CACHE)
    assert coinmetrics._cache_utilizavel().empty

    coinmetrics.salvar_snapshot(_bruto_ate(1), coinmetrics.CACHE)
    assert not coinmetrics._cache_utilizavel().empty


def test_snapshot_arredonda_para_o_git_nao_inchar(tmp_path):
    destino = str(tmp_path / "snap.csv")
    bruto = _bruto_ate(0)
    bruto.loc[0, "CapMVRVCur"] = 1.234567891234
    coinmetrics.salvar_snapshot(bruto, destino)
    lido = pd.read_csv(destino)
    assert lido["CapMVRVCur"].iloc[0] == 1.234568
    assert lido["date"].iloc[0].count("-") == 2   # data em YYYY-MM-DD
