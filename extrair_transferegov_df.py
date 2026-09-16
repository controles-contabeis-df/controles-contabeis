"""
Extracao completa: todas as informacoes do TransfereGov que fazem referencia
a alguma transferencia ao Distrito Federal, em qualquer papel (recebedor,
beneficiario, ente recebedor/repassador, proponente).

Cobre os 4 modulos:
  1. API Gestao de Parcerias  (convenios/parcerias novos - Plataforma+Brasil)
  2. API Transferencias Especiais (emendas parlamentares "transferencia especial")
  3. API Transferencias Fundo a Fundo (repasses diretos a fundos)
  4. Dados SICONV legado (convenios antigos, via download CSV)

Gera um CSV por tabela relevante, todos salvos na mesma pasta deste script.
Nao precisa de senha nem chave de API - sao dados abertos.
"""

import io
import time
import zipfile
from pathlib import Path

import requests
import pandas as pd

PASTA_SAIDA = Path(__file__).resolve().parent
UF_ALVO = "DF"


# ---------------------------------------------------------------------------
# Utilitarios genericos para as 3 APIs (Parcerias, Especiais, Fundo a Fundo)
# ---------------------------------------------------------------------------

def buscar_paginas(base_url: str, endpoint: str, params: dict, max_paginas: int = 300) -> list[dict]:
    registros = []
    pagina = 1
    while pagina <= max_paginas:
        params_pagina = {**params, "pagina": pagina, "tamanho_da_pagina": 100}
        resposta = requests.get(f"{base_url}/{endpoint}", params=params_pagina, timeout=30)
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


def buscar_por_id(base_url: str, endpoint: str, campo_id: str, valor_id) -> list[dict]:
    return buscar_paginas(base_url, endpoint, {campo_id: valor_id}, max_paginas=10)


def salvar(df: pd.DataFrame, nome_arquivo: str) -> None:
    caminho = PASTA_SAIDA / nome_arquivo
    df.to_csv(caminho, index=False, encoding="utf-8-sig")
    print(f"   -> {len(df)} registros salvos em {caminho.name}")


# ---------------------------------------------------------------------------
# 1) API Gestao de Parcerias
# ---------------------------------------------------------------------------

def extrair_parcerias():
    print("\n=== 1) GESTAO DE PARCERIAS ===")
    base = "https://api-publica.transferegov.gestao.gov.br/parcerias"

    propostas = buscar_paginas(base, "proposta", {"sg_uf_recebedor": UF_ALVO})
    df_propostas = pd.DataFrame(propostas)
    salvar(df_propostas, "df_parcerias_proposta.csv")

    parcerias, empenhos, documentos, pagamentos = [], [], [], []
    ids_proposta = df_propostas["id_proposta"].dropna().unique() if not df_propostas.empty else []

    for i, id_proposta in enumerate(ids_proposta, start=1):
        if i % 25 == 0:
            print(f"   processando proposta {i}/{len(ids_proposta)}...")
        lista_parcerias = buscar_por_id(base, "parceria", "id_proposta", id_proposta)
        parcerias.extend(lista_parcerias)
        for parceria in lista_parcerias:
            id_parceria = parceria.get("id_parceria")
            if not id_parceria:
                continue
            empenhos.extend(buscar_por_id(base, "empenho-parceria", "id_parceria", id_parceria))
            docs = buscar_por_id(base, "documento-habil", "id_parceria", id_parceria)
            documentos.extend(docs)
            for doc in docs:
                id_dh = doc.get("id_documento_habil")
                if id_dh:
                    pagamentos.extend(buscar_por_id(base, "ordem-pagamento", "id_documento_habil", id_dh))
        time.sleep(0.1)

    salvar(pd.DataFrame(parcerias), "df_parcerias_parceria.csv")
    salvar(pd.DataFrame(empenhos), "df_parcerias_empenho.csv")
    salvar(pd.DataFrame(documentos), "df_parcerias_documento_habil.csv")
    salvar(pd.DataFrame(pagamentos), "df_parcerias_ordem_pagamento.csv")


# ---------------------------------------------------------------------------
# 2) API Transferencias Especiais
# ---------------------------------------------------------------------------

def extrair_especiais():
    print("\n=== 2) TRANSFERENCIAS ESPECIAIS ===")
    base = "https://api-publica.transferegov.gestao.gov.br/especiais"

    beneficiarios = buscar_paginas(base, "beneficiarios-especiais", {"uf_beneficiario": UF_ALVO})
    df_beneficiarios = pd.DataFrame(beneficiarios)
    salvar(df_beneficiarios, "df_especiais_beneficiario.csv")

    planos, empenhos, documentos, pagamentos, executores, planos_trabalho = [], [], [], [], [], []
    ids_beneficiario = df_beneficiarios["id_beneficiario"].dropna().unique() if not df_beneficiarios.empty else []

    for i, id_beneficiario in enumerate(ids_beneficiario, start=1):
        planos_bnf = buscar_por_id(base, "planos-acao-especiais", "id_beneficiario", id_beneficiario)
        planos.extend(planos_bnf)
        for plano in planos_bnf:
            id_plano = plano.get("id_plano_acao")
            if not id_plano:
                continue
            executores.extend(buscar_por_id(base, "executores-especiais", "id_plano_acao", id_plano))
            # /planos-trabalho-especiais traz Situacao/Data Inicio/Data Fim de
            # Execucao (padrao do User Story do setor de Transparencia).
            planos_trabalho.extend(buscar_por_id(base, "planos-trabalho-especiais", "id_plano_acao", id_plano))
            emp = buscar_por_id(base, "empenhos-especiais", "id_plano_acao", id_plano)
            empenhos.extend(emp)
            for e in emp:
                id_empenho = e.get("id_empenho")
                if not id_empenho:
                    continue
                docs = buscar_por_id(base, "documentos-habeis-especiais", "id_empenho", id_empenho)
                documentos.extend(docs)
                for doc in docs:
                    id_dh = doc.get("id_dh")
                    if id_dh:
                        pagamentos.extend(
                            buscar_por_id(base, "ordens-pagamentos-ordens-bancarias-especiais", "id_dh", id_dh)
                        )
        time.sleep(0.1)

    salvar(pd.DataFrame(planos), "df_especiais_plano_acao.csv")
    salvar(pd.DataFrame(executores), "df_especiais_executor.csv")
    salvar(pd.DataFrame(planos_trabalho), "df_especiais_plano_trabalho.csv")
    salvar(pd.DataFrame(empenhos), "df_especiais_empenho.csv")
    salvar(pd.DataFrame(documentos), "df_especiais_documento_habil.csv")
    salvar(pd.DataFrame(pagamentos), "df_especiais_ordem_pagamento.csv")

    # /programas-especiais traz documentos_origem_programa, usado para excluir
    # registros de "Termo de Fomento" (conforme regra de negocio do setor de
    # Transparencia - recursos repassados direto a OSCs, sem transitar pelo GDF).
    df_planos = pd.DataFrame(planos)
    ids_programa = df_planos["id_programa"].dropna().unique() if not df_planos.empty and "id_programa" in df_planos.columns else []
    programas = []
    for id_programa in ids_programa:
        programas.extend(buscar_por_id(base, "programas-especiais", "id_programa", id_programa))
    salvar(pd.DataFrame(programas), "df_especiais_programa.csv")


# ---------------------------------------------------------------------------
# 3) API Transferencias Fundo a Fundo
# ---------------------------------------------------------------------------

def extrair_fundoafundo():
    print("\n=== 3) FUNDO A FUNDO ===")
    base = "https://api-publica.transferegov.gestao.gov.br/fundoafundo"

    # NOTA: uf_fundo_repassador_plano_acao e uf_ente_repassador_plano_acao NAO
    # servem para filtrar por DF - sao a UF de onde o fundo/ente federal
    # repassador esta sediado (normalmente Brasilia/DF para todo o Brasil),
    # entao usa-los como filtro captura o pais inteiro por engano.
    # So os campos de "recebedor"/"vinculado" identificam o DF como destino.
    filtros = [
        {"uf_ente_recebedor_plano_acao": UF_ALVO},
        {"uf_fundo_vinculado_plano_acao": UF_ALVO},
    ]
    planos = []
    for filtro in filtros:
        planos.extend(buscar_paginas(base, "planos-acao", filtro))

    df_planos = pd.DataFrame(planos)
    if not df_planos.empty and "id_plano_acao" in df_planos.columns:
        df_planos = df_planos.drop_duplicates(subset="id_plano_acao")
    salvar(df_planos, "df_fundoafundo_plano_acao.csv")

    # Cadeia de 4 tabelas para "Valor Pago" (regra de negocio do setor de
    # Transparencia - documento User Story Consulta Transferencias Federais):
    #   plano_acao -> planos-acao-dados-bancarios (id_plano_acao) -> id_agencia_conta
    #   -> gestao-financeira-lancamentos (id_agencia_conta) -> id_lancamento_gestao_financeira
    #      (+ descricao_origem_solicitacao_gestao_financeira, usado p/ excluir Termo de Fomento)
    #   -> gestao-financeira-subtransacoes (id_lancamento_gestao_financeira)
    #      -> somar valor_subtransacao_gestao_financeira
    ids_plano = df_planos["id_plano_acao"].dropna().unique() if not df_planos.empty else []
    dados_bancarios, lancamentos, subtransacoes = [], [], []

    for i, id_plano in enumerate(ids_plano, start=1):
        contas = buscar_por_id(base, "planos-acao-dados-bancarios", "id_plano_acao", id_plano)
        dados_bancarios.extend(contas)
        for conta in contas:
            id_agencia_conta = conta.get("id_agencia_conta")
            if not id_agencia_conta:
                continue
            lancs = buscar_por_id(base, "gestao-financeira-lancamentos", "id_agencia_conta", id_agencia_conta)
            lancamentos.extend(lancs)
            for lanc in lancs:
                id_lanc = lanc.get("id_lancamento_gestao_financeira")
                if id_lanc:
                    subtransacoes.extend(
                        buscar_por_id(base, "gestao-financeira-subtransacoes", "id_lancamento_gestao_financeira", id_lanc)
                    )
        if i % 20 == 0:
            print(f"   dados bancarios/financeiros: {i}/{len(ids_plano)} planos processados...")
        time.sleep(0.1)

    salvar(pd.DataFrame(dados_bancarios), "df_fundoafundo_dados_bancarios.csv")
    salvar(pd.DataFrame(lancamentos), "df_fundoafundo_lancamentos.csv")
    salvar(pd.DataFrame(subtransacoes), "df_fundoafundo_subtransacoes.csv")


# ---------------------------------------------------------------------------
# 4) SICONV legado (download CSV)
# ---------------------------------------------------------------------------

def baixar_csv_siconv(nome_arquivo: str, **kwargs) -> pd.DataFrame:
    url = f"https://api-publica.transferegov.gestao.gov.br/downloads/dadosgov/{nome_arquivo}.zip"
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    with z.open(z.namelist()[0]) as f:
        return pd.read_csv(f, sep=";", encoding="utf-8-sig", low_memory=False, **kwargs)


def extrair_siconv_legado():
    print("\n=== 4) SICONV LEGADO ===")

    print("   baixando siconv_proposta...")
    df_proposta = baixar_csv_siconv("siconv_proposta")
    df_proposta_df = df_proposta[df_proposta["UF_PROPONENTE"] == UF_ALVO].copy()
    salvar(df_proposta_df, "df_siconv_proposta.csv")

    print("   baixando siconv_convenio...")
    df_convenio = baixar_csv_siconv("siconv_convenio", dtype={"NR_CONVENIO": str})
    ids_proposta_df = set(df_proposta_df["ID_PROPOSTA"].dropna().unique())
    df_convenio_df = df_convenio[df_convenio["ID_PROPOSTA"].isin(ids_proposta_df)].copy()
    salvar(df_convenio_df, "df_siconv_convenio.csv")

    nrs_convenio_df = set(df_convenio_df["NR_CONVENIO"].dropna().astype(str).str.strip())

    print("   baixando siconv_empenho...")
    df_empenho = baixar_csv_siconv("siconv_empenho", dtype={"NR_CONVENIO": str})
    df_empenho_df = df_empenho[df_empenho["NR_CONVENIO"].astype(str).str.strip().isin(nrs_convenio_df)].copy()
    salvar(df_empenho_df, "df_siconv_empenho.csv")

    print("   baixando siconv_pagamento...")
    df_pagamento = baixar_csv_siconv("siconv_pagamento", dtype={"NR_CONVENIO": str})
    df_pagamento_df = df_pagamento[df_pagamento["NR_CONVENIO"].astype(str).str.strip().isin(nrs_convenio_df)].copy()
    salvar(df_pagamento_df, "df_siconv_pagamento.csv")

    print("   baixando siconv_emenda...")
    df_emenda = baixar_csv_siconv("siconv_emenda")
    df_emenda_df = df_emenda[df_emenda["ID_PROPOSTA"].isin(ids_proposta_df)].copy()
    salvar(df_emenda_df, "df_siconv_emenda.csv")


# ---------------------------------------------------------------------------

def main():
    extrair_parcerias()
    extrair_especiais()
    extrair_fundoafundo()
    extrair_siconv_legado()
    print("\nExtracao concluida.")


if __name__ == "__main__":
    main()
