"""
Prototipo: busca dados reais da API publica do TransfereGov (modulo Gestao de Parcerias)
filtrados para o Distrito Federal, percorrendo a cadeia completa:

    proposta (filtrada por UF=DF)
        -> parceria (convenio/instrumento efetivamente firmado, via id_proposta)
            -> empenho-parceria (via id_parceria)
            -> documento-habil (via id_parceria)
                -> ordem-pagamento (via id_documento_habil)

Isso cobre tanto propostas em analise/elaboracao quanto PARCERIAS JA FIRMADAS
(convenios e instrumentos congeneres celebrados), que e o foco do painel.

Nao precisa de senha nem de chave de API - e um dado aberto.
"""

import time
from pathlib import Path

import requests
import pandas as pd

BASE_URL = "https://api-publica.transferegov.gestao.gov.br/parcerias"
PASTA_SAIDA = Path(__file__).resolve().parent

# Limite de propostas processadas na cadeia completa (so para o prototipo -
# evita fazer milhares de chamadas). Ajustar/remover quando formos rodar "para valer".
LIMITE_PROPOSTAS_CADEIA = 15


def buscar_paginas(endpoint: str, params: dict, max_paginas: int = 3) -> list[dict]:
    """Busca ate `max_paginas` paginas de um endpoint e junta os resultados numa lista."""
    registros = []
    pagina = 1
    while pagina <= max_paginas:
        params_pagina = {**params, "pagina": pagina, "tamanho_da_pagina": 50}
        resposta = requests.get(f"{BASE_URL}/{endpoint}", params=params_pagina, timeout=30)
        resposta.raise_for_status()
        corpo = resposta.json()

        dados = corpo.get("data", [])
        if not dados:
            break

        registros.extend(dados)

        if pagina >= corpo.get("total_pages", 1):
            break
        pagina += 1

    return registros


def buscar_por_id(endpoint: str, campo_id: str, valor_id) -> list[dict]:
    """Busca registros de um endpoint filtrando por um unico campo (ex: id_proposta=123)."""
    return buscar_paginas(endpoint, {campo_id: valor_id}, max_paginas=5)


def salvar(df: pd.DataFrame, nome_arquivo: str) -> None:
    caminho = PASTA_SAIDA / nome_arquivo
    df.to_csv(caminho, index=False, encoding="utf-8-sig")
    print(f"   -> {len(df)} registros salvos em {caminho}")


def main():
    print("1) Buscando PROPOSTAS com recebedor no Distrito Federal...")
    propostas = buscar_paginas("proposta", {"sg_uf_recebedor": "DF"}, max_paginas=8)
    df_propostas = pd.DataFrame(propostas)
    salvar(df_propostas, "propostas_df.csv")

    if df_propostas.empty:
        print("Nenhuma proposta encontrada - encerrando.")
        return

    print(f"\n2) Percorrendo a cadeia proposta -> parceria -> empenho/documento/pagamento")
    print(f"   (limitado a {LIMITE_PROPOSTAS_CADEIA} propostas neste prototipo)\n")

    ids_proposta = df_propostas["id_proposta"].dropna().unique()[:LIMITE_PROPOSTAS_CADEIA]

    parcerias, empenhos, documentos, pagamentos = [], [], [], []

    for i, id_proposta in enumerate(ids_proposta, start=1):
        print(f"   [{i}/{len(ids_proposta)}] proposta {id_proposta}")

        lista_parcerias = buscar_por_id("parceria", "id_proposta", id_proposta)
        parcerias.extend(lista_parcerias)

        for parceria in lista_parcerias:
            id_parceria = parceria.get("id_parceria")
            if not id_parceria:
                continue

            lista_empenhos = buscar_por_id("empenho-parceria", "id_parceria", id_parceria)
            empenhos.extend(lista_empenhos)

            lista_documentos = buscar_por_id("documento-habil", "id_parceria", id_parceria)
            documentos.extend(lista_documentos)

            for documento in lista_documentos:
                id_documento_habil = documento.get("id_documento_habil")
                if not id_documento_habil:
                    continue
                pagamentos.extend(
                    buscar_por_id("ordem-pagamento", "id_documento_habil", id_documento_habil)
                )

            time.sleep(0.2)  # gentileza com a API publica

    print()
    df_parcerias = pd.DataFrame(parcerias)
    salvar(df_parcerias, "parcerias_df.csv")

    df_empenhos = pd.DataFrame(empenhos)
    salvar(df_empenhos, "empenhos_parceria_df.csv")

    df_documentos = pd.DataFrame(documentos)
    salvar(df_documentos, "documentos_habeis_df.csv")

    df_pagamentos = pd.DataFrame(pagamentos)
    salvar(df_pagamentos, "ordens_pagamento_df.csv")

    print("\nResumo de colunas por endpoint:")
    for nome, df in [
        ("propostas", df_propostas),
        ("parcerias", df_parcerias),
        ("empenhos", df_empenhos),
        ("documentos_habeis", df_documentos),
        ("ordens_pagamento", df_pagamentos),
    ]:
        print(f"  {nome}: {len(df.columns)} colunas -> {list(df.columns) if not df.empty else '(vazio)'}")


if __name__ == "__main__":
    main()
