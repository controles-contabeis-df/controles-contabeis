"""
Protecao das extracoes: nunca substitui um arquivo com dados por um resultado vazio
ou muito menor (instabilidade da API de origem).

Regra (aprovada em 19/10/2026): se o arquivo anterior tem dados e o novo vem com
0 registros, ou com queda > 30% (quando o anterior tem >= 20 registros), o arquivo
anterior e MANTIDO e um alerta e registrado em logs/alertas_extracao_AAAAMMDD.txt
(lido pela rotina diaria, que envia o aviso por e-mail).
"""

from datetime import datetime
from pathlib import Path

import pandas as pd

LIMITE_QUEDA = 0.30       # queda maxima aceita em relacao ao arquivo anterior
MINIMO_PARA_PERCENTUAL = 20  # abaixo disso so o "zerou" dispara
PASTA = Path(__file__).resolve().parent


def arquivo_alertas() -> Path:
    pasta = PASTA / "logs"
    pasta.mkdir(exist_ok=True)
    return pasta / f"alertas_extracao_{datetime.now():%Y%m%d}.txt"


def registrar_alerta(msg: str) -> None:
    linha = f"[{datetime.now():%d/%m/%Y %H:%M:%S}] {msg}"
    print(f"   [ALERTA] {msg}")
    with open(arquivo_alertas(), "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def linhas_arquivo(caminho: Path) -> int:
    """Numero de registros do CSV existente (0 se nao existe ou so tem cabecalho)."""
    if not caminho.exists():
        return 0
    try:
        return len(pd.read_csv(caminho, dtype=str, low_memory=False))
    except (pd.errors.EmptyDataError, pd.errors.ParserError):
        return 0


def salvar_protegido(df: pd.DataFrame, caminho: Path, **kwargs) -> bool:
    """Grava df em caminho, exceto se o resultado for suspeito (ver topo). Retorna True se gravou."""
    antigo = linhas_arquivo(caminho)
    novo = len(df)
    suspeito = antigo > 0 and (
        novo == 0 or (antigo >= MINIMO_PARA_PERCENTUAL and novo < antigo * (1 - LIMITE_QUEDA))
    )
    if suspeito:
        registrar_alerta(
            f"{caminho.name}: extracao veio com {novo} registro(s) contra {antigo} no arquivo anterior "
            f"(queda de {100 * (antigo - novo) / antigo:.0f}%). Mantido o arquivo anterior."
        )
        return False
    df.to_csv(caminho, index=False, **kwargs)
    return True
