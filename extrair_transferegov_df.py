"""
Extracao completa: todas as informacoes do TransfereGov que fazem referencia
a alguma transferencia ao Distrito Federal, em qualquer papel (recebedor,
beneficiario, ente recebedor/repassador, proponente).

Cobre os 4 modulos:
  1. API Gestao de Parcerias  (convenios/parcerias novos - Plataforma+Brasil)
  2. API Transferencias Especiais (emendas parlamentares "transferencia especial")
  3. API Transferencias Fundo a Fundo (repasses diretos a fundos)
  4. Discricionarias e Legais (CSVs do SICONV/Transferegov, via download)

Gera um CSV por tabela relevante, todos salvos na mesma pasta deste script.
Nao precisa de senha nem chave de API - sao dados abertos.
"""

import io
import time
import zipfile
from pathlib import Path

import requests
import pandas as pd

from protecao_extracao import salvar_protegido

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
        try:
            resposta = requests.get(f"{base_url}/{endpoint}", params=params_pagina, timeout=30)
            resposta.raise_for_status()
        except requests.exceptions.RequestException as e:
            # Alguns registros individuais dao erro 500 persistente no lado da
            # API publica (nao e transitorio) - pula esse registro em vez de
            # derrubar a extracao inteira.
            print(f"   [aviso] falha em {endpoint} {params_pagina}: {e}")
            break
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
    # protecao_extracao: nao troca arquivo com dados por resultado vazio/muito menor (queda > 30%)
    if salvar_protegido(df, caminho, encoding="utf-8-sig"):
        print(f"   -> {len(df)} registros salvos em {caminho.name}")


# Colunas de cada arquivo de Transferencias Especiais. Se a API devolver vazio
# (ocorreu a partir de 17/10/2026: /planos-acao-especiais e /executores-especiais
# responderam 200 com 0 registros para todo o pais), o arquivo e gravado so com o
# cabecalho - sem ele o pandas dava EmptyDataError e o painel nao era gerado.
COLUNAS_ESPECIAIS = {
    "df_especiais_plano_acao.csv": ["id_plano_acao", "codigo_plano_acao", "ano_plano_acao", "modalidade_plano_acao", "situacao_plano_acao", "email_camara", "data_aceite_plano_acao", "codigo_banco_plano_acao", "codigo_situacao_dado_bancario_plano_acao", "descricao_situacao_dado_bancario_plano_acao", "nome_banco_plano_acao", "numero_agencia_plano_acao", "dv_agencia_plano_acao", "numero_conta_plano_acao", "dv_conta_plano_acao", "nome_parlamentar_emenda_plano_acao", "ano_emenda_parlamentar_plano_acao", "codigo_parlamentar_emenda_plano_acao", "sequencial_emenda_parlamentar_plano_acao", "numero_emenda_parlamentar_plano_acao", "codigo_emenda_parlamentar_formatado_plano_acao", "categoria_despesa_plano_acao", "codigo_descricao_areas_politicas_publicas_plano_acao", "descricao_programacao_orcamentaria_plano_acao", "motivo_impedimento_plano_acao", "valor_custeio_plano_acao", "valor_investimento_plano_acao", "id_agencia_conta", "id_beneficiario", "id_objeto", "nome_objeto", "detalhamento_objeto", "id_programa"],
    "df_especiais_executor.csv": ["id_executor", "cnpj_executor", "nome_executor", "objeto_executor", "vl_custeio_executor", "vl_investimento_executor", "ind_recursos_gerenciados_conta_especifica_executor", "codigo_banco_executor", "nome_banco_executor", "numero_agencia_executor", "numero_dv_agencia_executor", "nome_agencia_executor", "numero_conta_executor", "numero_dv_conta_executor", "codigo_situacao_dado_bancario_executor", "descricao_situacao_dado_bancario_executor", "id_plano_acao"],
    "df_especiais_plano_trabalho.csv": ["id_plano_trabalho", "dt_hora_situacao_plano_trabalho", "situacao_plano_trabalho", "ind_orcamento_proprio_plano_trabalho", "data_inicio_execucao_plano_trabalho", "data_fim_execucao_plano_trabalho", "prazo_execucao_meses_plano_trabalho", "id_plano_acao", "classificacao_orcamentaria_pt", "ind_justificativa_prorrogacao_atraso_pt", "ind_justificativa_prorrogacao_paralizacao_pt", "justificativa_prorrogacao_paralizacao_pt", "dt_hora_pt_aprovado", "ind_orgao_analises_pendentes"],
    "df_especiais_empenho.csv": ["id_empenho", "id_minuta_empenho", "numero_empenho", "situacao_empenho", "descricao_situacao_empenho", "tipo_documento_empenho", "descricao_tipo_documento_empenho", "status_processamento_empenho", "ug_responsavel_empenho", "ug_emitente_empenho", "descricao_ug_emitente_empenho", "fonte_recurso_empenho", "plano_interno_empenho", "ptres_empenho", "grupo_natureza_despesa_empenho", "natureza_despesa_empenho", "subitem_empenho", "categoria_despesa_empenho", "modalidade_despesa_empenho", "numero_ro_empenho", "data_emissao_empenho", "prioridade_desbloqueio_empenho", "valor_empenho", "id_plano_acao"],
    "df_especiais_documento_habil.csv": ["id_dh", "id_minuta_documento_habil", "numero_documento_habil", "situacao_dh", "descricao_situacao_dh", "tipo_documento_dh", "ug_emitente_dh", "descricao_ug_emitente_dh", "data_vencimento_dh", "data_emissao_dh", "ug_pagadora_dh", "descricao_ug_pagadora_dh", "variacao_patrimonial_diminuta_dh", "passivo_transferencia_constitucional_legal_dh", "descricao_centro_custo_dh", "codigo_siorg_centro_custo_dh", "mes_referencia_centro_custo_dh", "ano_referencia_centro_custo_dh", "mes_ano_referencia_centro_custo_dh", "ug_beneficiada_centro_custo_dh", "descricao_ug_beneficiada_centro_custo_dh", "valor_dh", "valor_rateio_dh", "valor_custo_centro_custo_dh", "id_empenho"],
    "df_especiais_ordem_pagamento.csv": ["id_op_ob", "data_emissao_op", "numero_ordem_pagamento", "vinculacao_op", "situacao_op", "descricao_situacao_op", "data_situacao_op", "data_emissao_ob", "numero_ordem_bancaria", "numero_ordem_lancamento", "data_assinatura_ordenador_despesa_ob", "data_assinatura_gestor_financeiro_ob", "id_dh"],
    "df_especiais_programa.csv": ["id_programa", "ano_programa", "modalidade_programa", "codigo_programa", "id_orgao_superior_programa", "sigla_orgao_superior_programa", "cnpj_orgao_superior_programa", "nome_orgao_superior_programa", "id_orgao_programa", "sigla_orgao_programa", "cnpj_orgao_programa", "nome_orgao_programa", "id_unidade_gestora_programa", "documentos_origem_programa", "id_unidade_orcamentaria_responsavel_programa", "data_inicio_ciencia_programa", "data_fim_ciencia_programa", "valor_necessidade_financeira_programa", "valor_total_disponibilizado_programa", "valor_impedido_programa", "valor_a_disponibilizar_programa", "valor_documentos_habeis_gerados_programa", "valor_obs_geradas_programa", "valor_disponibilidade_atual_programa"],
}


def salvar_especiais(registros, nome_arquivo: str) -> None:
    """Como salvar(), garantindo o cabecalho das colunas quando a API devolve vazio
    (a protecao central decide se mantem o arquivo anterior)."""
    df = pd.DataFrame(registros)
    if df.empty:
        df = pd.DataFrame(columns=COLUNAS_ESPECIAIS[nome_arquivo])
    salvar(df, nome_arquivo)


# ---------------------------------------------------------------------------
# 1) API Gestao de Parcerias
# ---------------------------------------------------------------------------

def extrair_parcerias():
    print("\n=== 1) GESTAO DE PARCERIAS ===")
    base = "https://api-publica.transferegov.gestao.gov.br/parcerias"

    propostas = buscar_paginas(base, "proposta", {"sg_uf_recebedor": UF_ALVO})
    df_propostas = pd.DataFrame(propostas)
    salvar(df_propostas, "df_parcerias_proposta.csv")

    # /programa traz nm_ente_repassador - o orgao/ministerio concedente do
    # programa (a proposta/parceria nao tem esse dado diretamente).
    programas = []
    ids_programa = df_propostas["id_programa"].dropna().unique() if not df_propostas.empty and "id_programa" in df_propostas.columns else []
    for id_programa in ids_programa:
        programas.extend(buscar_por_id(base, "programa", "id_programa", id_programa))
    salvar(pd.DataFrame(programas), "df_parcerias_programa.csv")

    parcerias, empenhos, documentos, pagamentos, contas = [], [], [], [], []
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
            # /parceria-conta traz a conta bancaria da parceria (tx_conta),
            # usada para cruzamento por numero de conta com o SIGGO (NUCONTA).
            contas.extend(buscar_por_id(base, "parceria-conta", "id_parceria", id_parceria))
        time.sleep(0.1)

    salvar(pd.DataFrame(parcerias), "df_parcerias_parceria.csv")
    salvar(pd.DataFrame(empenhos), "df_parcerias_empenho.csv")
    salvar(pd.DataFrame(documentos), "df_parcerias_documento_habil.csv")
    salvar(pd.DataFrame(pagamentos), "df_parcerias_ordem_pagamento.csv")
    salvar(pd.DataFrame(contas), "df_parcerias_conta.csv")


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

    salvar_especiais(planos, "df_especiais_plano_acao.csv")
    salvar_especiais(executores, "df_especiais_executor.csv")
    salvar_especiais(planos_trabalho, "df_especiais_plano_trabalho.csv")
    salvar_especiais(empenhos, "df_especiais_empenho.csv")
    salvar_especiais(documentos, "df_especiais_documento_habil.csv")
    salvar_especiais(pagamentos, "df_especiais_ordem_pagamento.csv")

    # /programas-especiais traz documentos_origem_programa, usado para excluir
    # registros de "Termo de Fomento" (conforme regra de negocio do setor de
    # Transparencia - recursos repassados direto a OSCs, sem transitar pelo GDF).
    df_planos = pd.DataFrame(planos)
    ids_programa = df_planos["id_programa"].dropna().unique() if not df_planos.empty and "id_programa" in df_planos.columns else []
    programas = []
    for id_programa in ids_programa:
        programas.extend(buscar_por_id(base, "programas-especiais", "id_programa", id_programa))
    salvar_especiais(programas, "df_especiais_programa.csv")


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
# 4) Discricionarias e Legais (download CSV)
# ---------------------------------------------------------------------------

def baixar_csv_siconv(nome_arquivo: str, **kwargs) -> pd.DataFrame:
    url = f"https://api-publica.transferegov.gestao.gov.br/downloads/dadosgov/{nome_arquivo}.zip"
    r = requests.get(url, timeout=90)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    with z.open(z.namelist()[0]) as f:
        return pd.read_csv(f, sep=";", encoding="utf-8-sig", low_memory=False, **kwargs)


def extrair_siconv_legado():
    print("\n=== 4) DISCRICIONARIAS E LEGAIS (CSV SICONV) ===")

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

def extrair_datas_atualizacao():
    """Data da ultima atualizacao publicada por cada fonte (para o painel
    mostrar a idade dos dados de origem)."""
    print("\n=== DATAS DE ATUALIZACAO DAS FONTES ===")
    base = "https://api-publica.transferegov.gestao.gov.br"
    linhas = []
    for nome, modulo in (("Gestão de Parcerias", "parcerias"), ("Transferências Especiais", "especiais"),
                         ("Fundo a Fundo", "fundoafundo")):
        try:
            r = requests.get(f"{base}/{modulo}/data-atualizacao", timeout=30)
            r.raise_for_status()
            linhas.append({"fonte": nome, "atualizado_em": r.json().get("data_ultima_atualizacao")})
        except Exception as e:
            print(f"   [aviso] data-atualizacao {modulo}: {e}")
    try:
        df = baixar_csv_siconv("data_carga_siconv")
        linhas.append({"fonte": "Discricionárias e Legais", "atualizado_em": str(df.iloc[0, 0]).strip()})
    except Exception as e:
        print(f"   [aviso] data_carga_siconv: {e}")
    salvar(pd.DataFrame(linhas), "df_data_atualizacao_fontes.csv")


def main():
    extrair_parcerias()
    extrair_especiais()
    extrair_fundoafundo()
    extrair_siconv_legado()
    extrair_datas_atualizacao()
    print("\nExtracao concluida.")


if __name__ == "__main__":
    main()
