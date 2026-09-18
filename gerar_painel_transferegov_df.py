"""
Gera o painel HTML consolidado (parcerias/convenios do GDF, situacao e valores)
a partir dos CSVs extraidos por extrair_transferegov_df.py.

Regras aplicadas (conforme decidido com a usuaria):
  - So entram registros cujo CNPJ (ente recebedor/beneficiario/proponente)
    bate com a lista de orgaos e entidades do GDF extraida de
    MIL2026.UNIDADEGESTORA (campo NUCGC) + o CNPJ do ente federativo DF.
    Registros de organizacoes privadas apenas sediadas em Brasilia sao
    descartados nesta etapa (nao aparecem no painel).
  - Uma aba por modulo do TransfereGov, com o maximo de detalhe disponivel:
    numero do instrumento, ente beneficiario, ente concedente, datas de
    celebracao/vigencia, situacao, valor global e valor repassado.
  - Filtros de topo por Ente, Ano e Situacao em cada aba.

Salva transferegov.html na mesma pasta.
"""

import json
import re
from pathlib import Path

import pandas as pd

PASTA = Path(__file__).resolve().parent


def carregar(nome, **kwargs):
    return pd.read_csv(PASTA / nome, low_memory=False, **kwargs)


def carregar_com_colunas(nome, colunas, **kwargs):
    """
    Como carregar(), mas garante que as colunas listadas existam mesmo se o
    CSV estiver vazio ou zerado (ex: quando uma extracao falhou por
    instabilidade da API de origem) - evita erro em vez de travar o resto
    do painel por causa de uma fonte fora do ar.
    """
    try:
        df = carregar(nome, **kwargs)
    except pd.errors.EmptyDataError:
        df = pd.DataFrame()
    for c in colunas:
        if c not in df.columns:
            df[c] = pd.NA
    return df


def num(serie):
    return pd.to_numeric(serie, errors="coerce").fillna(0)


def normalizar_cnpj(serie):
    return serie.astype(str).str.replace(r"\D", "", regex=True).str.zfill(14)


def carregar_cnpjs_gdf() -> set:
    caminho = PASTA / "cnpjs_gdf.csv"
    if not caminho.exists():
        return set()
    df = pd.read_csv(caminho, dtype=str)
    cnpjs = set(normalizar_cnpj(df["NUCGC"]))
    cnpjs.discard("00000000000000")
    return cnpjs


# Entidades do GDF que o SICONV cadastra com CNPJ diferente do que consta em
# UNIDADEGESTORA.NUCGC (ex.: a Secretaria de Turismo e a UG 310101 do SIGGo têm
# CNPJ 33.143.334/0001-73, mas no SICONV aparece 05.589.348/0001-80). Achados
# em 2026-09-18 conferindo a natureza jurídica "Administração Pública Estadual
# ou do Distrito Federal" dos proponentes com UF=DF que ficavam fora da lista.
# Ficaram de fora, por não serem do GDF: representações de outros estados em
# Brasília e a "Secretaria de Articulação para o Desenvolvimento do Entorno".
CNPJS_GDF_COMPLEMENTARES = {
    "05589348000180": "SECRETARIA DE EST. DE TURISMO DO DISTRITO FEDERAL",
    "16854222000101": "SECRETARIA ESPECIAL DA PROMOCAO DA IGUALDADE RACIAL DO DISTRITO FEDERAL",
    # Empresas distritais que nao sao UG do SIGGo mas firmaram instrumentos
    # com a Uniao; devem constar (transparencia/controle) mesmo se extintas.
    "08911986000163": "EMPRESA BRASILIENSE DE TURISMO",
    "00082024000137": "COMPANHIA DE SANEAMENTO AMBIENTAL DO DISTRITO FEDERAL",
}

CNPJS_GDF = carregar_cnpjs_gdf() | set(CNPJS_GDF_COMPLEMENTARES)


def eh_gdf(serie_cnpj: pd.Series) -> pd.Series:
    return normalizar_cnpj(serie_cnpj).isin(CNPJS_GDF)


def eh_gdf_qualquer_formato(serie, ugs_df: set) -> pd.Series:
    """
    Em MIL2026.TRANSFERENCIA, tanto COCONCENTE quanto COBENEFICIADO podem vir
    em dois formatos: CNPJ puro (14 digitos) OU codigo "UG-sequencial" (ex:
    "210101-00001"). Reconhece o GDF nos dois formatos.
    """
    serie_str = serie.astype(str).str.strip()
    cnpj_norm = normalizar_cnpj(serie)
    prefixo_ug = serie_str.str.split("-").str[0]
    return cnpj_norm.isin(CNPJS_GDF) | prefixo_ug.isin(ugs_df)


# Concedentes que aparecem no papel "Uniao -> GDF" (COCONCENTE != GDF) mas que,
# verificados um a um na Receita Federal em 2026-09-17, NAO sao orgaos/entidades
# da Uniao: empresas distritais (Terracap, Caesb, BRB), organismos
# internacionais, fundacoes/associacoes privadas, outro municipio, e codigos
# "EX" que representam emprestimos internacionais tomados pelo proprio GDF
# (nao repasse da Uniao). Mantidos no escopo (por decisao da usuaria): bancos
# e empresas 100% federais atuando como agente financeiro/mandatario de
# repasses da Uniao (Caixa, Banco do Brasil, BNDES, Correios, Embrapa, FINEP,
# CBTU) - conferido que o objeto desses registros cita "recursos da Uniao".
CONCEDENTES_NAO_UNIAO = {
    "00359877000173",  # Terracap
    "00082024000137",  # Caesb
    "00000208000100",  # BRB - Banco de Brasilia
    "07522669000192",  # Neoenergia Distribuicao Brasilia
    "82777301000190",  # Municipio de Lages/SC
    "03723329000179",  # PNUD
    "03736617000168",  # UNESCO
    "04389228000176",  # BID
    "41950369000142",  # FONPLATA
    "00328072000162",  # Instituto Ayrton Senna
    "11028900000163",  # Instituto Incubadora
    "10912323000105",  # Instituto Campus Party
    "33641663000306",  # Fundacao Getulio Vargas
    "01641000000133",  # Fundacao Banco do Brasil
    "26424671000173",  # Cooperativa Habit. do Pessoal da CEF
}
CONCEDENTES_NAO_UNIAO_COD = {
    "EX0000010", "EX0000001",
    # "150105-00001" e "090101-00001": codigos UG que nao existem na
    # MIL2026.UNIDADEGESTORA atual, mas cujo objeto e claramente do proprio
    # GDF (aterro sanitario do DF/SLU; manutencao do Palacio do Buriti) -
    # provavel auto-relacionamento com codigo de UG desativado/legado,
    # investigado a pedido da usuaria em 2026-09-17. NAO inclui "540101-00001"
    # porque o objeto desse registro cita explicitamente "IBRAM-MINISTERIO DO
    # TURISMO", confirmando que e um concedente federal legitimo.
    "150105-00001", "090101-00001",
}


def eh_concedente_nao_uniao(serie) -> pd.Series:
    serie_str = serie.astype(str).str.strip()
    cnpj_norm = normalizar_cnpj(serie)
    return cnpj_norm.isin(CONCEDENTES_NAO_UNIAO) | serie_str.isin(CONCEDENTES_NAO_UNIAO_COD)


def _parse_data(serie):
    """
    Converte a serie para datetime tentando primeiro o formato ISO (usado pelas
    APIs do TransfereGov: AAAA-MM-DD, com ou sem hora) e so depois o formato
    brasileiro dd/mm/aaaa (usado nos CSVs do SICONV). Fazer as duas tentativas
    numa unica chamada com dayfirst=True causa falhas intermitentes de parse
    em datas ISO validas (bug observado: ~1/3 das datas de vigencia do Fundo
    a Fundo viravam NaT mesmo sendo strings AAAA-MM-DD perfeitamente validas).
    """
    s = serie.astype(str)
    dt = pd.to_datetime(s, format="ISO8601", errors="coerce")
    faltando = dt.isna() & serie.notna()
    if faltando.any():
        dt.loc[faltando] = pd.to_datetime(s[faltando], dayfirst=True, errors="coerce")
    return dt


def fmt_data(serie):
    """Converte para dd/mm/aaaa quando possivel, mantendo string original se falhar."""
    dt = _parse_data(serie)
    saida = dt.dt.strftime("%d/%m/%Y")
    return saida.where(dt.notna(), serie)


def extrair_ano(serie):
    return _parse_data(serie).dt.year


# ---------------------------------------------------------------------------
# 1) Gestao de Parcerias
# ---------------------------------------------------------------------------

def montar_parcerias():
    proposta = carregar("df_parcerias_proposta.csv")
    parceria = carregar_com_colunas(
        "df_parcerias_parceria.csv",
        ["id_proposta", "id_parceria", "cd_parceria", "in_situacao_parceria", "dh_assinatura", "cd_processo_sei"],
    )
    doc_habil = carregar_com_colunas("df_parcerias_documento_habil.csv", ["id_documento_habil", "id_parceria"])
    ordem_pag = carregar_com_colunas("df_parcerias_ordem_pagamento.csv", ["id_documento_habil", "vl_ordem_pagamento"])
    programa = carregar_com_colunas("df_parcerias_programa.csv", ["id_programa", "nm_ente_repassador"])

    # valor repassado = soma das ordens de pagamento, agregadas por parceria
    # (ordem_pagamento -> id_documento_habil -> documento_habil -> id_parceria)
    if not ordem_pag.empty and not doc_habil.empty:
        pag = ordem_pag.merge(
            doc_habil[["id_documento_habil", "id_parceria"]], on="id_documento_habil", how="left"
        )
        repasse_por_parceria = pag.groupby("id_parceria")["vl_ordem_pagamento"].sum()
    else:
        repasse_por_parceria = pd.Series(dtype=float)

    df = proposta.merge(
        parceria[["id_proposta", "id_parceria", "cd_parceria", "in_situacao_parceria", "dh_assinatura", "cd_processo_sei"]],
        on="id_proposta", how="left",
    )
    df = df.merge(programa[["id_programa", "nm_ente_repassador"]], on="id_programa", how="left")
    df = df[eh_gdf(df["cnpj_ente_recebedor"])].copy()

    df["situacao"] = df["in_situacao_parceria"].fillna(df["situacao_proposta"])
    df["valor_global"] = num(df.get("nr_vlr_total")).where(
        num(df.get("nr_vlr_total")) > 0, num(df.get("vl_total_planejamento_gastos"))
    )
    df["valor_repassado"] = df["id_parceria"].map(repasse_por_parceria).fillna(0)

    saida = pd.DataFrame({
        "Nº Parceria": df["cd_parceria"].fillna(df["id_proposta"].astype("Int64").astype(str)),
        "Concedente": df["nm_ente_repassador"],
        "Beneficiário": df["nm_ente_recebedor"],
        "Objeto": df["ds_objeto"].astype(str).str.slice(0, 100),
        "Situação": df["situacao"],
        "Data de celebração": fmt_data(df["dh_assinatura"]),
        "Ano": df["ano_proposta"],
        "Valor global (R$)": df["valor_global"],
        "Valor repassado (R$)": df["valor_repassado"],
    })
    params = {
        "Nº Parceria": "parceria.cd_parceria",
        "Concedente": "programa.nm_ente_repassador",
        "Beneficiário": "proposta.nm_ente_recebedor",
        "Objeto": "proposta.ds_objeto",
        "Situação": "parceria.in_situacao_parceria",
        "Data de celebração": "parceria.dh_assinatura",
        "Ano": "proposta.ano_proposta",
        "Valor global (R$)": "proposta.nr_vlr_total",
        "Valor repassado (R$)": "Σ ordem-pagamento.vl_ordem_pagamento",
    }
    return saida.sort_values("Ano", ascending=False, na_position="last"), params


# ---------------------------------------------------------------------------
# 2) Transferencias Especiais
# ---------------------------------------------------------------------------

def categoria_despesa(custeio, investimento):
    c = num(custeio)
    i = num(investimento)
    def classifica(cv, iv):
        if cv == 0 and iv == 0:
            return ""
        if iv == 0:
            return "Custeio"
        if cv == 0:
            return "Investimento"
        return "Custeio e Investimento"
    return pd.Series([classifica(cv, iv) for cv, iv in zip(c, i)], index=custeio.index if hasattr(custeio, "index") else None)


def montar_especiais():
    plano = carregar("df_especiais_plano_acao.csv")
    beneficiario = carregar("df_especiais_beneficiario.csv")
    empenho = carregar("df_especiais_empenho.csv")
    doc_habil = carregar("df_especiais_documento_habil.csv")
    ordem_pag = carregar("df_especiais_ordem_pagamento.csv")
    executor = carregar("df_especiais_executor.csv")
    plano_trabalho = carregar("df_especiais_plano_trabalho.csv")
    programa = carregar_com_colunas("df_especiais_programa.csv", ["id_programa", "nome_orgao_programa"])

    colunas = ["Nº Plano de Ação", "Nº Emenda Parlamentar", "Concedente", "Beneficiário", "Órgão executor",
               "Parlamentar autor da emenda", "Situação", "Data de celebração", "Início execução", "Fim execução",
               "Ano", "Valor global (R$)", "Valor pago (R$)"]
    params = {
        "Nº Plano de Ação": "plano_acao.codigo_plano_acao",
        "Nº Emenda Parlamentar": "plano_acao.numero_emenda_parlamentar_plano_acao",
        "Concedente": "programa.nome_orgao_programa",
        "Beneficiário": "beneficiario.nome_beneficiario",
        "Órgão executor": "executor.nome_executor",
        "Parlamentar autor da emenda": "plano_acao.nome_parlamentar_emenda_plano_acao",
        "Situação": "plano_acao.situacao_plano_acao",
        "Data de celebração": "plano_acao.data_aceite_plano_acao",
        "Início execução": "plano_trabalho.data_inicio_execucao_plano_trabalho",
        "Fim execução": "plano_trabalho.data_fim_execucao_plano_trabalho",
        "Ano": "plano_acao.ano_plano_acao",
        "Valor global (R$)": "valor_custeio_plano_acao + valor_investimento_plano_acao",
        "Valor pago (R$)": "Σ documento-habil.valor_dh (c/ ordem-pagamento ENVIADA·PAGO)",
    }
    if plano.empty:
        return pd.DataFrame(columns=colunas), params

    # "Ente beneficiário" formal e sempre DISTRITO FEDERAL (emenda especial cai
    # direto na conta unica do ente federativo - ver memoria project_transferegov
    # _gdf_vs_privado). "Orgao executor" (endpoint /executores-especiais) traz a
    # secretaria/orgao que de fato executa o recurso - e o dado especifico que
    # faltava.
    if not executor.empty:
        executor_por_plano = executor.drop_duplicates(subset="id_plano_acao").set_index("id_plano_acao")["nome_executor"]
    else:
        executor_por_plano = pd.Series(dtype=str)

    if not plano_trabalho.empty:
        pt = plano_trabalho.sort_values("id_plano_trabalho").drop_duplicates(subset="id_plano_acao", keep="last")
        pt = pt.set_index("id_plano_acao")
        inicio_por_plano = pt["data_inicio_execucao_plano_trabalho"]
        fim_por_plano = pt["data_fim_execucao_plano_trabalho"]
    else:
        inicio_por_plano = pd.Series(dtype=str)
        fim_por_plano = pd.Series(dtype=str)

    # Valor pago (regra do setor de Transparencia): somar valor_dh dos documentos
    # habeis cuja ordem de pagamento tenha situacao contendo ENVIADA ou PAGO,
    # seguindo a cadeia empenho -> documento_habil -> ordem_pagamento.
    if not doc_habil.empty and not ordem_pag.empty:
        situacao_ok = ordem_pag["descricao_situacao_op"].astype(str).str.upper().str.contains("ENVIADA|PAGO", na=False)
        ids_dh_pagos = set(ordem_pag.loc[situacao_ok, "id_dh"].dropna())
        doc_pago = doc_habil[doc_habil["id_dh"].isin(ids_dh_pagos)]
        doc_pago = doc_pago.merge(empenho[["id_empenho", "id_plano_acao"]], on="id_empenho", how="left")
        valor_pago_por_plano = doc_pago.groupby("id_plano_acao")["valor_dh"].sum()
    else:
        valor_pago_por_plano = pd.Series(dtype=float)

    plano = plano.merge(beneficiario[["id_beneficiario", "nome_beneficiario", "cnpj_beneficiario"]], on="id_beneficiario", how="left")
    plano = plano.merge(programa[["id_programa", "nome_orgao_programa"]], on="id_programa", how="left")
    plano = plano[eh_gdf(plano["cnpj_beneficiario"])].copy()
    plano["valor"] = num(plano.get("valor_custeio_plano_acao")) + num(plano.get("valor_investimento_plano_acao"))
    plano["valor_pago"] = plano["id_plano_acao"].map(valor_pago_por_plano).fillna(0)
    plano["orgao_executor"] = plano["id_plano_acao"].map(executor_por_plano).fillna("")
    plano["inicio_execucao"] = plano["id_plano_acao"].map(inicio_por_plano)
    plano["fim_execucao"] = plano["id_plano_acao"].map(fim_por_plano)

    saida = pd.DataFrame({
        "Nº Plano de Ação": plano["codigo_plano_acao"],
        "Nº Emenda Parlamentar": plano["numero_emenda_parlamentar_plano_acao"],
        "Concedente": plano["nome_orgao_programa"],
        "Beneficiário": plano["nome_beneficiario"],
        "Órgão executor": plano["orgao_executor"],
        "Parlamentar autor da emenda": plano["nome_parlamentar_emenda_plano_acao"],
        "Situação": plano["situacao_plano_acao"],
        "Data de celebração": fmt_data(plano.get("data_aceite_plano_acao")),
        "Início execução": fmt_data(plano["inicio_execucao"]),
        "Fim execução": fmt_data(plano["fim_execucao"]),
        "Ano": plano["ano_plano_acao"],
        "Valor global (R$)": plano["valor"],
        "Valor pago (R$)": plano["valor_pago"],
    })
    return saida.sort_values("Ano", ascending=False, na_position="last"), params


# ---------------------------------------------------------------------------
# 3) Fundo a Fundo
# ---------------------------------------------------------------------------

def montar_fundoafundo():
    plano = carregar("df_fundoafundo_plano_acao.csv")
    dados_bancarios = carregar("df_fundoafundo_dados_bancarios.csv")
    lancamentos = carregar("df_fundoafundo_lancamentos.csv")
    subtransacoes = carregar("df_fundoafundo_subtransacoes.csv")

    colunas = ["Nº Plano de Ação", "Concedente", "Beneficiário", "Fundo vinculado",
               "Situação", "Data de celebração", "Início vigência", "Fim vigência", "Ano",
               "Valor global (R$)", "Valor repassado (R$)", "Valor pago (R$)"]
    params = {
        "Nº Plano de Ação": "plano_acao.codigo_plano_acao",
        "Concedente": "plano_acao.nome_orgao_repassador_plano_acao",
        "Beneficiário": "plano_acao.nome_ente_recebedor_plano_acao",
        "Fundo vinculado": "plano_acao.nome_fundo_vinculado_plano_acao",
        "Situação": "plano_acao.situacao_plano_acao",
        "Data de celebração": "plano_acao.data_inicio_vigencia_plano_acao (proxy - API não tem data de assinatura própria)",
        "Início vigência": "plano_acao.data_inicio_vigencia_plano_acao",
        "Fim vigência": "plano_acao.data_fim_vigencia_plano_acao",
        "Ano": "ano(data_inicio_vigencia_plano_acao)",
        "Valor global (R$)": "plano_acao.valor_total_plano_acao",
        "Valor repassado (R$)": "plano_acao.valor_total_repasse_plano_acao",
        "Valor pago (R$)": "Σ subtransações.valor_subtransacao_gestao_financeira",
    }
    if plano.empty:
        return pd.DataFrame(columns=colunas), params

    # Valor pago (cadeia de 4 tabelas - regra do setor de Transparencia):
    # plano_acao -> dados_bancarios (id_agencia_conta) -> lancamentos
    # (id_lancamento_gestao_financeira) -> subtransacoes -> somar valor_subtransacao
    if not dados_bancarios.empty and not lancamentos.empty and not subtransacoes.empty:
        sub_valor = subtransacoes.groupby("id_lancamento_gestao_financeira")["valor_subtransacao_gestao_financeira"].sum()
        lanc = lancamentos.copy()
        lanc["valor_pago_lanc"] = lanc["id_lancamento_gestao_financeira"].map(sub_valor).fillna(0)
        lanc_por_conta = lanc.groupby("id_agencia_conta")["valor_pago_lanc"].sum()
        origem_por_conta = lanc.groupby("id_agencia_conta")["descricao_origem_solicitacao_gestao_financeira"].agg(
            lambda s: s.dropna().iloc[0] if s.dropna().size else ""
        )
        db = dados_bancarios.copy()
        db["valor_pago_conta"] = db["id_agencia_conta"].map(lanc_por_conta).fillna(0)
        db["origem_conta"] = db["id_agencia_conta"].map(origem_por_conta).fillna("")
        valor_pago_por_plano = db.groupby("id_plano_acao")["valor_pago_conta"].sum()
        origem_por_plano = db.groupby("id_plano_acao")["origem_conta"].agg(lambda s: s[s != ""].iloc[0] if (s != "").any() else "")
    else:
        valor_pago_por_plano = pd.Series(dtype=float)
        origem_por_plano = pd.Series(dtype=str)

    plano = plano[eh_gdf(plano["cnpj_ente_recebedor_plano_acao"])].copy()
    plano["valor_pago"] = plano["id_plano_acao"].map(valor_pago_por_plano).fillna(0)
    plano["origem"] = plano["id_plano_acao"].map(origem_por_plano).fillna("")

    saida = pd.DataFrame({
        "Nº Plano de Ação": plano["codigo_plano_acao"],
        "Concedente": plano["nome_orgao_repassador_plano_acao"],
        "Beneficiário": plano["nome_ente_recebedor_plano_acao"],
        "Fundo vinculado": plano["nome_fundo_vinculado_plano_acao"],
        "Situação": plano["situacao_plano_acao"],
        "Data de celebração": fmt_data(plano["data_inicio_vigencia_plano_acao"]),
        "Início vigência": fmt_data(plano["data_inicio_vigencia_plano_acao"]),
        "Fim vigência": fmt_data(plano["data_fim_vigencia_plano_acao"]),
        "Ano": extrair_ano(plano["data_inicio_vigencia_plano_acao"]),
        "Valor global (R$)": num(plano.get("valor_total_plano_acao")),
        "Valor repassado (R$)": num(plano.get("valor_total_repasse_plano_acao")),
        "Valor pago (R$)": plano["valor_pago"],
    })
    return saida.sort_values("Ano", ascending=False, na_position="last"), params


# ---------------------------------------------------------------------------
# 4) Discricionárias e Legais (CSVs do SICONV / Transferegov)
# ---------------------------------------------------------------------------

def montar_siconv():
    convenio = carregar("df_siconv_convenio.csv", dtype={"NR_CONVENIO": str})
    proposta = carregar("df_siconv_proposta.csv")
    emenda = carregar("df_siconv_emenda.csv")

    # Uma proposta pode ter mais de uma emenda vinculada - agregamos numero(s)
    # e parlamentar(es) em uma unica celula, separados por " | ".
    if not emenda.empty:
        emenda_agg = emenda.groupby("ID_PROPOSTA").agg({
            "NR_EMENDA": lambda s: " | ".join(sorted(set(s.dropna().astype(str)))),
            "NOME_PARLAMENTAR": lambda s: " | ".join(sorted(set(s.dropna().astype(str)))),
            "TIPO_PARLAMENTAR": lambda s: " | ".join(sorted(set(s.dropna().astype(str)))),
        }).reset_index()
    else:
        emenda_agg = pd.DataFrame(columns=["ID_PROPOSTA", "NR_EMENDA", "NOME_PARLAMENTAR", "TIPO_PARLAMENTAR"])

    df = convenio.merge(
        proposta[["ID_PROPOSTA", "DESC_ORGAO_SUP", "NM_PROPONENTE", "MUNIC_PROPONENTE", "IDENTIF_PROPONENTE", "MODALIDADE"]],
        on="ID_PROPOSTA", how="left",
    )
    df = df.merge(emenda_agg, on="ID_PROPOSTA", how="left")
    df = df[eh_gdf(df["IDENTIF_PROPONENTE"])].copy()

    # Regra de negocio (decisao da usuaria): excluir Termo de Fomento - recursos
    # da Uniao a OSCs, sem transitar pelo GDF. Na pratica ja fica 100% excluido
    # pelo filtro de CNPJ do proponente (Termo de Fomento e sempre com OSC), mas
    # o filtro explicito fica aqui como garantia caso a base mude no futuro.
    df = df[~df["MODALIDADE"].astype(str).str.upper().eq("TERMO DE FOMENTO")].copy()

    saida = pd.DataFrame({
        "Nº Convênio": df["NR_CONVENIO"],
        "Concedente": df["DESC_ORGAO_SUP"],
        "Beneficiário": df["NM_PROPONENTE"],
        "Modalidade": df["MODALIDADE"],
        "Situação": df["SIT_CONVENIO"],
        "Data de celebração": fmt_data(df["DIA_ASSIN_CONV"]),
        "Início vigência": fmt_data(df["DIA_INIC_VIGENC_CONV"]),
        "Fim vigência": fmt_data(df["DIA_FIM_VIGENC_CONV"]),
        "Ano": df["ANO"],
        "Valor global (R$)": num(df.get("VL_GLOBAL_CONV")),
        "Valor repasse (R$)": num(df.get("VL_REPASSE_CONV")),
        "Valor empenhado (R$)": num(df.get("VL_EMPENHADO_CONV")),
        "Valor pago (R$)": num(df.get("VL_DESEMBOLSADO_CONV")),
        "Nº Emenda Parlamentar": df["NR_EMENDA"].fillna(""),
        "Parlamentar autor da emenda": df["NOME_PARLAMENTAR"].fillna(""),
    })
    params = {
        "Nº Convênio": "convenio.NR_CONVENIO",
        "Concedente": "proposta.DESC_ORGAO_SUP",
        "Beneficiário": "proposta.NM_PROPONENTE",
        "Modalidade": "proposta.MODALIDADE",
        "Situação": "convenio.SIT_CONVENIO",
        "Data de celebração": "convenio.DIA_ASSIN_CONV",
        "Início vigência": "convenio.DIA_INIC_VIGENC_CONV",
        "Fim vigência": "convenio.DIA_FIM_VIGENC_CONV",
        "Ano": "convenio.ANO",
        "Valor global (R$)": "convenio.VL_GLOBAL_CONV",
        "Valor repasse (R$)": "convenio.VL_REPASSE_CONV",
        "Valor empenhado (R$)": "convenio.VL_EMPENHADO_CONV",
        "Valor pago (R$)": "convenio.VL_DESEMBOLSADO_CONV",
        "Nº Emenda Parlamentar": "emenda.NR_EMENDA",
        "Parlamentar autor da emenda": "emenda.NOME_PARLAMENTAR",
    }
    return saida.sort_values("Ano", ascending=False, na_position="last"), params


# ---------------------------------------------------------------------------
# 5) SIGGO — tabela MIL2026.TRANSFERENCIA (para cruzamento futuro)
# ---------------------------------------------------------------------------

# Tabela de dominio oficial "Especie de Transferencia" do SIGGO (fornecida
# pela usuaria via tela do sistema em 2026-09-16 - nao existe tabela de
# lookup consultavel via SQL no schema MIL2026).
ESPECIE_LABELS = {
    1: "Convênio", 2: "Acordo", 3: "Ajuste", 4: "Auxílio", 5: "Subvenção",
    6: "Contribuição", 7: "Termo de Outorga e Aceitação", 8: "Termo de Fomento",
    9: "Termo de Colaboração", 10: "Transferência Especial", 11: "PDAF",
    12: "Fundo a Fundo", 13: "Contrato de Repasse", 14: "Contrato de Gestão",
    15: "Operação de Crédito",
}


def montar_siggo():
    t = carregar("df_siggo_transferencia.csv")
    colunas = ["Nº Transferência", "Espécie", "Nº SICONV/SIAFI", "Nº Original",
               "Concedente", "Beneficiário", "UG registrante", "Objeto",
               "Data de celebração", "Início vigência", "Fim vigência",
               "Valor transferência (R$)", "Valor contrapartida (R$)"]
    params = {
        "Nº Transferência": "TRANSFERENCIA.NUTRANSFERENCIA",
        "Espécie": "TRANSFERENCIA.INESPECIE (tabela de domínio do SIGGO)",
        "Nº SICONV/SIAFI": "TRANSFERENCIA.NUTRANSFSIAFI",
        "Nº Original": "TRANSFERENCIA.NUORIGINAL",
        "Concedente": "TRANSFERENCIA.COCONCENTE",
        "Beneficiário": "TRANSFERENCIA.COBENEFICIADO + UNIDADEGESTORA.NOUG",
        "UG registrante": "TRANSFERENCIA.COUG + UNIDADEGESTORA.NOUG",
        "Objeto": "TRANSFERENCIA.TXOBJETORESUMIDO",
        "Data de celebração": "TRANSFERENCIA.DACELEBRACAO",
        "Início vigência": "TRANSFERENCIA.DAINIVIGENCIA",
        "Fim vigência": "TRANSFERENCIA.DAFIMVIGENCIA",
        "Valor transferência (R$)": "TRANSFERENCIA.VATRANSFERENCIA",
        "Valor contrapartida (R$)": "TRANSFERENCIA.VACONTRAPARTIDA",
    }
    if t.empty:
        return pd.DataFrame(columns=colunas), params

    ug = carregar("unidadegestora_bruto.csv", dtype=str)
    ug_nome = ug.drop_duplicates(subset="COUG").set_index("COUG")["NOUG"].str.strip()
    ugs_df = set(ug["COUG"].str.strip())

    # Filtro por PAPEL (nao por especie): so entram registros em que o
    # concedente NAO e o GDF e o beneficiario E o GDF - ou seja, recurso
    # externo (essencialmente Uniao) recebido pelo GDF. COCONCENTE e
    # COBENEFICIADO podem vir em CNPJ puro OU codigo "UG-sequencial" (os
    # dois formatos sao reconhecidos). Isso exclui automaticamente os casos
    # em que o GDF e concedente (repassa a privados/OSCs ou a si mesmo) sem
    # precisar de uma lista de especies para bloquear - testado empiricamente
    # em 2026-09-16: 6 especies (Auxilio, Subvencao, Contribuicao, Termo de
    # Outorga e Aceitacao, PDAF, Contrato de Gestao) tem ZERO casos de "Uniao
    # -> GDF" na base toda, mas outras como Termo de Fomento tem alguns raros
    # casos legitimos que uma lista de exclusao por especie perderia.
    concedente_e_gdf = eh_gdf_qualquer_formato(t["COCONCENTE"], ugs_df)
    beneficiario_e_gdf = eh_gdf_qualquer_formato(t["COBENEFICIADO"], ugs_df)
    # Alem de excluir GDF como concedente, exclui tambem concedentes
    # confirmados como NAO sendo da Uniao (empresas distritais, organismos
    # internacionais, fundacoes privadas etc. - ver CONCEDENTES_NAO_UNIAO) -
    # caso encontrado pela usuaria em 2026-09-17 (Terracap aparecendo como
    # concedente de uma transferencia a NOVACAP).
    concedente_nao_uniao = eh_concedente_nao_uniao(t["COCONCENTE"])
    t = t[(~concedente_e_gdf) & (~concedente_nao_uniao) & beneficiario_e_gdf].copy()

    t["especie"] = t["INESPECIE"].map(
        lambda v: f"{int(v):02d} - {ESPECIE_LABELS.get(int(v), 'Código ' + str(int(v)))}" if pd.notna(v) else ""
    )

    prefixo_benef = t["COBENEFICIADO"].astype(str).str.strip().str.split("-").str[0]
    nome_benef = prefixo_benef.map(ug_nome)
    t["beneficiario_fmt"] = t["COBENEFICIADO"].astype(str).str.strip()
    tem_nome = nome_benef.notna()
    t.loc[tem_nome, "beneficiario_fmt"] = nome_benef[tem_nome] + " (" + t.loc[tem_nome, "COBENEFICIADO"].astype(str).str.strip() + ")"

    coug_str = t["COUG"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    nome_coug = coug_str.map(ug_nome)
    t["coug_fmt"] = coug_str
    tem_nome_coug = nome_coug.notna()
    t.loc[tem_nome_coug, "coug_fmt"] = nome_coug[tem_nome_coug] + " (" + coug_str[tem_nome_coug] + ")"

    nutransfsiafi = pd.to_numeric(t["NUTRANSFSIAFI"], errors="coerce")

    saida = pd.DataFrame({
        "Nº Transferência": t["NUTRANSFERENCIA"],
        "Espécie": t["especie"],
        "Nº SICONV/SIAFI": nutransfsiafi.where(nutransfsiafi > 0, "").astype(str).replace("0.0", ""),
        "Nº Original": t["NUORIGINAL"],
        "Concedente": t["COCONCENTE"],
        "Beneficiário": t["beneficiario_fmt"],
        "UG registrante": t["coug_fmt"],
        "Objeto": t["TXOBJETORESUMIDO"].astype(str).str.slice(0, 120),
        "Data de celebração": fmt_data(t["DACELEBRACAO"]),
        "Início vigência": fmt_data(t["DAINIVIGENCIA"]),
        "Fim vigência": fmt_data(t["DAFIMVIGENCIA"]),
        "Valor transferência (R$)": num(t["VATRANSFERENCIA"]),
        "Valor contrapartida (R$)": num(t["VACONTRAPARTIDA"]),
    })
    return saida.sort_values("Nº Transferência", ascending=False), params


# ---------------------------------------------------------------------------
# 6) Cruzamento SIGGO x TransfereGov
# ---------------------------------------------------------------------------

def _nome_canonico(serie_cnpj, cnpj_para_nome):
    """CNPJ (qualquer formato) -> nome oficial da UG (NOUG), quando reconhecido."""
    return normalizar_cnpj(serie_cnpj).map(cnpj_para_nome)


def _so_digitos(serie):
    return serie.astype(str).str.replace(r"\D", "", regex=True)


def montar_cruzamento():
    """
    Para cada registro do SIGGO (ja filtrado por papel: Uniao->GDF), tenta
    achar a que instrumento do TransfereGov ele corresponde, em ordem de
    confianca decrescente (chave mais robusta primeiro):
      1) EXATA: NUTRANSFSIAFI == NR_CONVENIO no SICONV (chave validada em
         project_transferegov_siggo_chave).
      2) SUBSTRING: os digitos de NUORIGINAL contem (ou estao contidos em)
         os digitos do identificador da fonte (cd_parceria / codigo_plano_
         acao) - so considerado quando o trecho comum tem 6+ digitos, para
         evitar coincidencia trivial.
      3) BENEFICIARIO+DATA+VALOR: nome oficial da UG beneficiaria (via CNPJ,
         mesma fonte MIL2026.UNIDADEGESTORA dos dois lados) + ano da data de
         celebracao/vigencia + valor batem juntos - so disponivel para Fundo
         a Fundo, unica fonte com data de vigencia bem preenchida.
      4) BENEFICIARIO+VALOR: nome oficial da UG beneficiaria + valor batem
         juntos, sem exigir data.
      5) VALOR (sem beneficiario): ultimo recurso, mais sujeito a falso
         positivo em valores redondos/repetidos (ver memoria
         project_lacuna_saude_fundoafundo).
      6) SEM CORRESPONDENCIA: nao achou em nenhuma fonte extraida.
    """
    t = carregar("df_siggo_transferencia.csv")
    colunas = ["Nº Transf.", "Espécie", "Concedente", "Beneficiário", "Objeto", "Data Celeb.", "Valor (R$)",
               "Nº SICONV/SIAFI", "Nº Original", "Encontrado em", "Confiança", "Indicador"]
    params = {
        "Nº Transf.": "TRANSFERENCIA.NUTRANSFERENCIA",
        "Espécie": "TRANSFERENCIA.INESPECIE",
        "Concedente": "TRANSFERENCIA.COCONCENTE",
        "Beneficiário": "TRANSFERENCIA.COBENEFICIADO + UNIDADEGESTORA.NOUG",
        "Objeto": "TRANSFERENCIA.TXOBJETORESUMIDO",
        "Data Celeb.": "TRANSFERENCIA.DACELEBRACAO",
        "Valor (R$)": "TRANSFERENCIA.VATRANSFERENCIA",
        "Nº SICONV/SIAFI": "TRANSFERENCIA.NUTRANSFSIAFI",
        "Nº Original": "TRANSFERENCIA.NUORIGINAL",
        "Encontrado em": "resultado do cruzamento (ver metodologia no rodapé)",
        "Confiança": "ver hierarquia de camadas no rodapé (Nº SICONV/SIAFI > substrings > soma NDx > benef.+data/ano/valor > benef.+objeto)",
        "Indicador": "NR_CONVENIO / cd_parceria / codigo_plano_acao, conforme a fonte",
    }
    if t.empty:
        return pd.DataFrame(columns=colunas), params

    ug = carregar("unidadegestora_bruto.csv", dtype=str)
    ugs_df = set(ug["COUG"].str.strip())
    ug_nome = ug.drop_duplicates(subset="COUG").set_index("COUG")["NOUG"].str.strip()
    cnpjs_gdf_df = carregar("cnpjs_gdf.csv", dtype=str)
    cnpj_para_nome = cnpjs_gdf_df.drop_duplicates(subset="NUCGC").assign(
        NUCGC=lambda d: normalizar_cnpj(d["NUCGC"])
    ).set_index("NUCGC")["NOUG"].str.strip()
    cnpj_para_nome = cnpj_para_nome.combine_first(pd.Series(CNPJS_GDF_COMPLEMENTARES))

    concedente_e_gdf = eh_gdf_qualquer_formato(t["COCONCENTE"], ugs_df)
    beneficiario_e_gdf = eh_gdf_qualquer_formato(t["COBENEFICIADO"], ugs_df)
    # Alem de excluir GDF como concedente, exclui tambem concedentes
    # confirmados como NAO sendo da Uniao (empresas distritais, organismos
    # internacionais, fundacoes privadas etc. - ver CONCEDENTES_NAO_UNIAO) -
    # caso encontrado pela usuaria em 2026-09-17 (Terracap aparecendo como
    # concedente de uma transferencia a NOVACAP).
    concedente_nao_uniao = eh_concedente_nao_uniao(t["COCONCENTE"])
    t = t[(~concedente_e_gdf) & (~concedente_nao_uniao) & beneficiario_e_gdf].copy()

    t["especie"] = t["INESPECIE"].map(
        lambda v: f"{int(v):02d} - {ESPECIE_LABELS.get(int(v), 'Código ' + str(int(v)))}" if pd.notna(v) else ""
    )
    prefixo_benef = t["COBENEFICIADO"].astype(str).str.strip().str.split("-").str[0]
    nome_benef = prefixo_benef.map(ug_nome)
    # COBENEFICIADO tambem pode vir em CNPJ puro (sem "-", nao e um codigo de
    # UG) - nesse caso o map por COUG acima nunca acha nada. Complementa com
    # busca por CNPJ (mesma fonte cnpj_para_nome usada em GP/FaF/Especiais) -
    # bug encontrado pela usuaria em 2026-09-17: NUTRANSFERENCIA 23612 exibia
    # o CNPJ cru "03658028000109" em vez do nome da UG.
    nome_benef_cnpj = normalizar_cnpj(t["COBENEFICIADO"]).map(cnpj_para_nome)
    nome_benef = nome_benef.fillna(nome_benef_cnpj)
    t["beneficiario_fmt"] = t["COBENEFICIADO"].astype(str).str.strip()
    tem_nome = nome_benef.notna()
    t.loc[tem_nome, "beneficiario_fmt"] = nome_benef[tem_nome] + " (" + t.loc[tem_nome, "COBENEFICIADO"].astype(str).str.strip() + ")"
    t["nome_canonico_siggo"] = nome_benef  # nome oficial da UG, sem sufixo - usado nas camadas de benef.

    # Nome da UG REGISTRANTE (COUG) - em casos de saude/educacao, o usuario do
    # SIGGO costuma cadastrar a Secretaria como COBENEFICIADO, mas quem de
    # fato recebe/executa na API (TransfereGov) e o Fundo (Fundo de Saude/
    # Educacao), que corresponde ao COUG do lancamento. Caso encontrado pela
    # usuaria em 2026-09-17: NUTRANSFERENCIA 30849 (COBENEFICIADO=Secretaria
    # de Saude, COUG=Fundo de Saude do DF) deveria bater com a parceria
    # 202500042786 (recebedor = FUNDO DE SAUDE DO DISTRITO FEDERAL na API).
    coug_str = t["COUG"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    t["nome_canonico_coug"] = coug_str.map(ug_nome)
    t["nutransfsiafi_limpo"] = pd.to_numeric(t["NUTRANSFSIAFI"], errors="coerce").fillna(0).astype("int64").astype(str)
    t["nuoriginal_digitos"] = _so_digitos(t["NUORIGINAL"])
    t["ano_celebracao"] = extrair_ano(t["DACELEBRACAO"])
    t["ano_vigencia"] = extrair_ano(t["DAINIVIGENCIA"])

    # --- Fonte 1: SICONV (chave exata) ---
    convenio = carregar("df_siconv_convenio.csv", dtype={"NR_CONVENIO": str})
    proposta_sic = carregar("df_siconv_proposta.csv")
    sic = convenio.merge(proposta_sic[["ID_PROPOSTA", "IDENTIF_PROPONENTE", "CD_CONTA"]], on="ID_PROPOSTA", how="left")
    sic = sic[eh_gdf(sic["IDENTIF_PROPONENTE"])]
    nrs_siconv = set(sic["NR_CONVENIO"].dropna().astype(str).str.strip())

    # NAO usa Nº de Emenda Parlamentar do SICONV como identificador de
    # substring: testado em 2026-09-17 e descartado - "emendas de bancada"
    # (ex: 71080005/71080006) financiam VARIAS propostas SICONV ao mesmo
    # tempo (5-6 propostas por emenda), entao bater so pelo nº da emenda
    # associa a transferencia a uma proposta arbitraria/errada, sequestrando
    # o caso da camada 4 antes que a camada 8 (Beneficiário+Valor, mais
    # precisa) pudesse achar a fonte certa. Diferente de Transferências
    # Especiais, onde numero_emenda_parlamentar_plano_acao e quase sempre
    # unico (ver digitos_esp_emenda) - por isso esse continua em uso.

    # --- Fonte 2: Gestao de Parcerias ---
    proposta_gp = carregar("df_parcerias_proposta.csv")
    parceria_gp = carregar_com_colunas("df_parcerias_parceria.csv", ["id_proposta", "id_parceria", "cd_parceria"])
    gp = proposta_gp.merge(parceria_gp[["id_proposta", "cd_parceria"]], on="id_proposta", how="left")
    gp = gp[eh_gdf(gp["cnpj_ente_recebedor"])].copy()
    gp["valor_planej"] = num(gp["vl_total_planejamento_gastos"]).round(2)
    gp["nome_canonico"] = _nome_canonico(gp["cnpj_ente_recebedor"], cnpj_para_nome)
    gp["cd_parceria_str"] = gp["cd_parceria"].dropna().astype("Int64").astype(str)
    gp["ano_proposta"] = pd.to_numeric(gp.get("ano_proposta"), errors="coerce")
    gp["objeto_upper"] = gp["ds_objeto"].astype(str).str.upper().str.strip()
    # So considera linhas com identificador valido - se a extracao de /parceria
    # falhou (fonte fora do ar), cd_parceria_str fica todo NaN e NENHUMA
    # tabela de cruzamento abaixo deve ser criada, senao viram "matches" com
    # identificador vazio (falso positivo).
    gp_valido = gp[gp["cd_parceria_str"].notna()]
    ids_cd_parceria = gp["cd_parceria_str"].dropna().unique().tolist()
    valor_para_parceria = gp_valido[gp_valido["valor_planej"] > 0].groupby("valor_planej")["cd_parceria_str"].apply(list)
    benef_valor_para_parceria = gp_valido[(gp_valido["valor_planej"] > 0) & gp_valido["nome_canonico"].notna()].groupby(
        ["nome_canonico", "valor_planej"]
    )["cd_parceria_str"].apply(list)
    benef_ano_valor_para_parceria = gp_valido[
        (gp_valido["valor_planej"] > 0) & gp_valido["nome_canonico"].notna() & gp_valido["ano_proposta"].notna()
    ].groupby(["nome_canonico", "ano_proposta", "valor_planej"])["cd_parceria_str"].apply(list)
    benef_objeto_para_parceria = gp_valido[gp_valido["nome_canonico"].notna() & (gp_valido["objeto_upper"].str.len() >= 15)].groupby(
        "nome_canonico"
    ).apply(lambda g: list(zip(g["cd_parceria_str"], g["objeto_upper"])))
    conta_gp = None
    try:
        conta_gp_df = carregar_com_colunas("df_parcerias_conta.csv", ["id_parceria", "tx_conta"])
        conta_gp_df = conta_gp_df.merge(parceria_gp[["id_parceria", "cd_parceria"]], on="id_parceria", how="left")
        conta_gp_df["conta_digitos"] = _so_digitos(conta_gp_df["tx_conta"])
        conta_gp_df["cd_parceria_str"] = conta_gp_df["cd_parceria"].dropna().astype("Int64").astype(str)
        conta_gp = conta_gp_df[conta_gp_df["conta_digitos"].str.len() >= 4]
    except FileNotFoundError:
        conta_gp = pd.DataFrame(columns=["conta_digitos", "cd_parceria_str"])

    # --- Fonte 3: Fundo a Fundo ---
    faf = carregar("df_fundoafundo_plano_acao.csv")
    faf = faf[eh_gdf(faf["cnpj_ente_recebedor_plano_acao"])].copy()
    faf["valor_repasse"] = num(faf.get("valor_total_repasse_plano_acao")).round(2)
    faf["nome_canonico"] = _nome_canonico(faf["cnpj_ente_recebedor_plano_acao"], cnpj_para_nome)
    faf["ano_vigencia"] = extrair_ano(faf["data_inicio_vigencia_plano_acao"])
    faf["codigo_digitos"] = _so_digitos(faf["codigo_plano_acao"])
    faf["objeto_upper"] = faf["objetivos_plano_acao"].astype(str).str.upper().str.strip()
    valor_para_faf = faf[faf["valor_repasse"] > 0].groupby("valor_repasse")["codigo_plano_acao"].apply(list)
    benef_valor_para_faf = faf[(faf["valor_repasse"] > 0) & faf["nome_canonico"].notna()].groupby(
        ["nome_canonico", "valor_repasse"]
    )["codigo_plano_acao"].apply(list)
    # Data exata (dia/mes/ano) de inicio de vigencia - camada mais forte que ano
    faf["data_inicio_str"] = fmt_data(faf["data_inicio_vigencia_plano_acao"])
    benef_data_exata_valor_para_faf = faf[
        (faf["valor_repasse"] > 0) & faf["nome_canonico"].notna() & (faf["data_inicio_str"] != "")
    ].groupby(["nome_canonico", "data_inicio_str", "valor_repasse"])["codigo_plano_acao"].apply(list)
    benef_ano_valor_para_faf = faf[
        (faf["valor_repasse"] > 0) & faf["nome_canonico"].notna() & faf["ano_vigencia"].notna()
    ].groupby(["nome_canonico", "ano_vigencia", "valor_repasse"])["codigo_plano_acao"].apply(list)
    benef_objeto_para_faf = faf[faf["nome_canonico"].notna() & (faf["objeto_upper"].str.len() >= 15)].groupby(
        "nome_canonico"
    ).apply(lambda g: list(zip(g["codigo_plano_acao"], g["objeto_upper"])))
    try:
        conta_faf_df = carregar("df_fundoafundo_dados_bancarios.csv")
        conta_faf_df["conta_digitos"] = _so_digitos(conta_faf_df.get("numero_conta_plano_acao_dado_bancario", pd.Series(dtype=str)))
        conta_faf_df = conta_faf_df.merge(faf[["id_plano_acao", "codigo_plano_acao"]], on="id_plano_acao", how="left")
        conta_faf = conta_faf_df[conta_faf_df["conta_digitos"].str.len() >= 4]
    except FileNotFoundError:
        conta_faf_df = pd.DataFrame(columns=["id_agencia_conta", "id_plano_acao"])
        conta_faf = pd.DataFrame(columns=["conta_digitos", "codigo_plano_acao"])

    # Fonte adicional de conta bancaria do Fundo a Fundo: os lancamentos
    # financeiros (/gestao-financeira-lancamentos) tambem trazem
    # codigo_conta_gestao_financeira, ligado ao plano de acao via
    # id_agencia_conta (mesmo vinculo de planos-acao-dados-bancarios).
    try:
        lanc_faf = carregar_com_colunas(
            "df_fundoafundo_lancamentos.csv", ["id_agencia_conta", "codigo_conta_gestao_financeira"]
        )
        lanc_faf["conta_digitos"] = _so_digitos(lanc_faf["codigo_conta_gestao_financeira"])
        mapa_agencia_plano = conta_faf_df[["id_agencia_conta", "id_plano_acao"]].dropna().drop_duplicates()
        lanc_faf = lanc_faf.merge(mapa_agencia_plano, on="id_agencia_conta", how="left")
        lanc_faf = lanc_faf.merge(faf[["id_plano_acao", "codigo_plano_acao"]], on="id_plano_acao", how="left")
        conta_faf_lanc = lanc_faf[
            (lanc_faf["conta_digitos"].str.len() >= 4) & lanc_faf["codigo_plano_acao"].notna()
        ]
    except FileNotFoundError:
        conta_faf_lanc = pd.DataFrame(columns=["conta_digitos", "codigo_plano_acao"])

    # --- Fonte 4: Transferencias Especiais ---
    esp_plano = carregar("df_especiais_plano_acao.csv")
    esp_benef = carregar("df_especiais_beneficiario.csv")
    esp = esp_plano.merge(esp_benef[["id_beneficiario", "cnpj_beneficiario"]], on="id_beneficiario", how="left")
    esp = esp[eh_gdf(esp["cnpj_beneficiario"])].copy()
    esp["valor_total"] = (num(esp["valor_custeio_plano_acao"]) + num(esp["valor_investimento_plano_acao"])).round(2)
    esp["nome_canonico"] = _nome_canonico(esp["cnpj_beneficiario"], cnpj_para_nome)
    esp["codigo_digitos"] = _so_digitos(esp["codigo_plano_acao"])
    esp["emenda_digitos"] = _so_digitos(esp.get("numero_emenda_parlamentar_plano_acao", pd.Series(dtype=str)))
    esp["objeto_upper"] = esp["nome_objeto"].astype(str).str.upper().str.strip()
    esp["ano_plano_acao"] = pd.to_numeric(esp.get("ano_plano_acao"), errors="coerce")
    # O "beneficiario" formal de Transferencias Especiais e sempre o ente
    # (DISTRITO FEDERAL), mas quem de fato recebe/executa e o Orgao Executor
    # (ex: Secretaria de Saude) - e e esse nome que costuma aparecer em
    # COBENEFICIADO no SIGGO. Usa o executor como beneficiario alternativo.
    esp_exec = carregar_com_colunas(
        "df_especiais_executor.csv",
        ["id_plano_acao", "cnpj_executor", "vl_custeio_executor", "vl_investimento_executor"],
    )
    esp_exec["valor_executor"] = (num(esp_exec["vl_custeio_executor"]) + num(esp_exec["vl_investimento_executor"])).round(2)
    esp_exec["nome_canonico_executor"] = _nome_canonico(esp_exec["cnpj_executor"], cnpj_para_nome)
    esp = esp.merge(
        esp_exec[["id_plano_acao", "nome_canonico_executor", "valor_executor"]], on="id_plano_acao", how="left"
    )
    valor_para_esp = esp[esp["valor_total"] > 0].groupby("valor_total")["codigo_plano_acao"].apply(list)
    benef_valor_para_esp = esp[(esp["valor_total"] > 0) & esp["nome_canonico"].notna()].groupby(
        ["nome_canonico", "valor_total"]
    )["codigo_plano_acao"].apply(list)
    benef_valor_para_esp_executor = esp[
        (esp["valor_executor"] > 0) & esp["nome_canonico_executor"].notna()
    ].groupby(["nome_canonico_executor", "valor_executor"])["codigo_plano_acao"].apply(list)
    benef_ano_valor_para_esp = esp[
        (esp["valor_total"] > 0) & esp["nome_canonico"].notna() & esp["ano_plano_acao"].notna()
    ].groupby(["nome_canonico", "ano_plano_acao", "valor_total"])["codigo_plano_acao"].apply(list)
    benef_objeto_para_esp = esp[esp["nome_canonico"].notna() & (esp["objeto_upper"].str.len() >= 15)].groupby(
        "nome_canonico"
    ).apply(lambda g: list(zip(g["codigo_plano_acao"], g["objeto_upper"])))
    try:
        conta_esp = esp[_so_digitos(esp["numero_conta_plano_acao"]).str.len() >= 4].copy()
        conta_esp["conta_digitos"] = _so_digitos(conta_esp["numero_conta_plano_acao"])
    except KeyError:
        conta_esp = pd.DataFrame(columns=["conta_digitos", "codigo_plano_acao"])

    # Listas de digitos para busca por substring (ordenadas da mais longa p/
    # a mais curta, para preferir o match mais especifico primeiro). Inclui
    # o SICONV tambem - antes so procurava em Gestao de Parcerias/Fundo a
    # Fundo/Especiais, e por isso perdia casos como NUTRANSFERENCIA 31017
    # (NUORIGINAL "CONVÊNIO 941097/2023" deveria ter batido com o Nº Convênio
    # 941097 do SICONV, mas o SICONV nunca era testado nessa busca).
    digitos_siconv = sorted(nrs_siconv, key=len, reverse=True)
    digitos_parceria = sorted(set(gp["cd_parceria_str"].dropna()), key=len, reverse=True)
    digitos_faf = sorted(set(faf["codigo_digitos"].dropna()), key=len, reverse=True)
    digitos_esp = sorted(set(esp["codigo_digitos"].dropna()), key=len, reverse=True)
    # Nº da Emenda Parlamentar tambem e usado como identificador em NUORIGINAL
    # (ex: NUTRANSFERENCIA com NUORIGINAL "202443780013" = numero_emenda_
    # parlamentar_plano_acao de Transferencias Especiais, sem nenhuma relacao
    # com codigo_plano_acao) - caso encontrado pela usuaria em 2026-09-17.
    digitos_esp_emenda = sorted(set(esp["emenda_digitos"].dropna()), key=len, reverse=True)
    FONTES_SUBSTRING = ((digitos_siconv, "Discricionárias e Legais"),
                         (digitos_parceria, "Gestão de Parcerias"),
                         (digitos_faf, "Fundo a Fundo"),
                         (digitos_esp, "Transferências Especiais"),
                         (digitos_esp_emenda, "Transferências Especiais"))

    def busca_substring(digitos_campo):
        """Procura um identificador de qualquer fonte dentro dos digitos do
        campo informado (ou vice-versa) - usada para NUORIGINAL."""
        if len(digitos_campo) < 6:
            return None
        for candidatos, fonte in FONTES_SUBSTRING:
            for cod in candidatos:
                if len(cod) >= 6 and (cod in digitos_campo or digitos_campo in cod):
                    return fonte, cod
        return None

    # Indice de contas bancarias (digitos) para busca por substring com NUCONTA
    contas_index = []
    # Conta bancaria do SICONV (CD_CONTA, em siconv_proposta): so a conta do
    # convenio/CR do GDF, ate entao nao usada no cruzamento por conta.
    # Comparacao EXATA (sem zeros a esquerda), nao por substring: testado em
    # 2026-09-18, por substring so 51% dos casos eram coerentes em valor/ano
    # (numeros curtos de conta "caem dentro" de contas longas do SIGGo); por
    # igualdade exata, 99% (276 de 280).
    conta_sic = sic.assign(conta_digitos=_so_digitos(sic["CD_CONTA"]).str.lstrip("0"))
    conta_sic = conta_sic[conta_sic["conta_digitos"].str.len() >= 4]
    conta_sic_exata = conta_sic.groupby("conta_digitos")["NR_CONVENIO"].apply(lambda s: sorted(set(s.str.strip())))
    if not conta_gp.empty:
        for _, r in conta_gp.iterrows():
            if r["conta_digitos"] and pd.notna(r.get("cd_parceria_str")):
                contas_index.append((r["conta_digitos"], "Gestão de Parcerias", r["cd_parceria_str"]))
    if not conta_faf.empty:
        for _, r in conta_faf.iterrows():
            if r["conta_digitos"] and pd.notna(r.get("codigo_plano_acao")):
                contas_index.append((r["conta_digitos"], "Fundo a Fundo", r["codigo_plano_acao"]))
    if not conta_faf_lanc.empty:
        for _, r in conta_faf_lanc.iterrows():
            if r["conta_digitos"] and pd.notna(r.get("codigo_plano_acao")):
                contas_index.append((r["conta_digitos"], "Fundo a Fundo", r["codigo_plano_acao"]))
    if not conta_esp.empty:
        for _, r in conta_esp.iterrows():
            if r["conta_digitos"] and pd.notna(r.get("codigo_plano_acao")):
                contas_index.append((r["conta_digitos"], "Transferências Especiais", r["codigo_plano_acao"]))
    contas_index.sort(key=lambda x: len(x[0]), reverse=True)

    def busca_substring_conta(nuconta_dig):
        if len(nuconta_dig) < 4:
            return None
        for cod, fonte, ident in contas_index:
            if len(cod) >= 4 and (cod in nuconta_dig or nuconta_dig in cod):
                return fonte, ident
        return None

    def busca_objeto(objeto_siggo, nome):
        if pd.isna(nome) or len(objeto_siggo) < 15:
            return None
        for tabela, fonte in ((benef_objeto_para_parceria, "Gestão de Parcerias"),
                              (benef_objeto_para_faf, "Fundo a Fundo"),
                              (benef_objeto_para_esp, "Transferências Especiais")):
            if nome not in tabela.index:
                continue
            for ident, objeto_fonte in tabela.loc[nome]:
                if len(objeto_fonte) >= 15 and (objeto_fonte in objeto_siggo or objeto_siggo in objeto_fonte):
                    return fonte, ident
        return None

    # Extracao ANCORADA por palavra-chave dos numeros de identificacao citados
    # no objeto (ex: "SICONV Nº 825427/2015") - NAO extrai qualquer sequencia
    # de digitos do texto. Testado em 2026-09-17: extrair todo digito solto
    # do objeto (ex: de um numero de emenda parlamentar como "43850001")
    # gerava falso positivo por coincidencia numerica com um convenio real
    # mas sem nenhuma relacao (3 de 4 casos testados eram falso positivo).
    PADRAO_ID_OBJETO = re.compile(
        r"(?:SICONV|CONV[EÊ]NIO|CONV\.|CONTRATO\s*DE\s*REPASSE|PARCERIA|PLANO\s*DE\s*A[CÇ][AÃ]O|EMENDA(?:\s*PARLAMENTAR)?)"
        r"\s*N?[ºO°]?\.?\s*[:\-]?\s*([\d./\-]{6,})",
        re.IGNORECASE,
    )

    def extrair_ids_objeto(texto):
        achados = []
        for m in PADRAO_ID_OBJETO.finditer(str(texto).upper()):
            digs = re.sub(r"\D", "", m.group(1))
            if len(digs) >= 6:
                achados.append(digs)
        return achados

    def busca_objeto_numerico(ids_extraidos):
        for dig in ids_extraidos:
            achado = busca_substring(dig)
            if achado:
                return achado
        return None

    t["nuconta_digitos"] = _so_digitos(t.get("NUCONTA", pd.Series(dtype=str)))
    # Conta compartilhada: uma mesma conta usada por muitos registros do SIGGo
    # (ex.: conta unica do Fundo de Saude, que recebe repasses de varios
    # instrumentos) nao identifica um instrumento especifico. Testado em
    # 2026-09-18: incluir a conta do SICONV fez o convenio 979160 "absorver" 36
    # registros de Saude de anos diferentes - falso positivo. So vale o match por
    # conta quando ela e usada por no maximo 5 registros (aditivos/parcelas).
    t["nuconta_qtd"] = t.groupby(t["nuconta_digitos"].str.lstrip("0"))["NUTRANSFERENCIA"].transform("size")
    t["objeto_upper"] = t["TXOBJETORESUMIDO"].astype(str).str.upper().str.strip()
    t["objeto_ids"] = t["TXOBJETORESUMIDO"].apply(extrair_ids_objeto)

    # Caso encontrado pela usuaria em 2026-09-17: Termos de Adesao (Fundo a
    # Fundo) as vezes sao lancados no SIGGO como 2 registros separados, um
    # por "Nivel de Desempenho" (NUORIGINAL termina em "-ND3"/"-ND4"/etc),
    # cada um com uma fracao do valor. A API so tem o plano_acao com o VALOR
    # TOTAL (ND3+ND4 somados), entao nenhum registro isolado bate sozinho -
    # so a SOMA dos registros do mesmo concedente e mesmo NUORIGINAL sem o
    # sufixo "-NDx" bate com o valor_total_repasse do plano de acao.
    # Ex: NUORIGINAL "TA 21/2025-MQV-ND3" (R$ 2.041.202,07) + "TA 21/2025-
    # MQV-ND4" (R$ 2.041.202,07) = R$ 4.082.404,14 = valor_total_repasse do
    # plano de acao 00905320250002-021705 (Secretaria Nacional de Seguranca
    # Publica).
    t["nuoriginal_upper"] = t["NUORIGINAL"].astype(str).str.strip().str.upper()
    t["nuoriginal_prefixo_nd"] = t["nuoriginal_upper"].str.replace(r"-ND\d+$", "", regex=True)
    tem_sufixo_nd = (t["nuoriginal_prefixo_nd"] != t["nuoriginal_upper"]) & (t["nuoriginal_prefixo_nd"] != "")
    t["soma_grupo_nd"] = pd.NA
    if tem_sufixo_nd.any():
        soma_por_linha = t[tem_sufixo_nd].groupby(["COCONCENTE", "nuoriginal_prefixo_nd"])["VATRANSFERENCIA"].transform(
            lambda s: round(s.sum(), 2)
        )
        t.loc[tem_sufixo_nd, "soma_grupo_nd"] = soma_por_linha

    def cruzar(row):
        # 1) Nº SICONV/SIAFI (chave exata)
        if row["nutransfsiafi_limpo"] != "0" and row["nutransfsiafi_limpo"] in nrs_siconv:
            return pd.Series(["Discricionárias e Legais", "1 - Nº SICONV/SIAFI", row["nutransfsiafi_limpo"]])

        # 2) Substring (NUORIGINAL x identificador de qualquer fonte, incl. SICONV)
        achado = busca_substring(row["nuoriginal_digitos"])
        if achado:
            fonte, cod = achado
            return pd.Series([fonte, "2 - Substring (Nº Original)", cod])

        # 3) Substring de conta bancária (NUCONTA)
        achado_conta = None
        if row["nuconta_qtd"] <= 5:
            nc = row["nuconta_digitos"].lstrip("0")
            if nc in conta_sic_exata.index and len(conta_sic_exata.loc[nc]) == 1:
                achado_conta = ("Discricionárias e Legais", conta_sic_exata.loc[nc][0])
            else:
                achado_conta = busca_substring_conta(row["nuconta_digitos"])
        if achado_conta:
            fonte, cod = achado_conta
            return pd.Series([fonte, "3 - Substring (Conta Banc.)", cod])

        # 4) Substring de Objeto (procura um nº SICONV/parceria/plano de ação
        # embutido no texto do objeto do SIGGO)
        achado_obj_num = busca_objeto_numerico(row["objeto_ids"])
        if achado_obj_num:
            fonte, cod = achado_obj_num
            return pd.Series([fonte, "4 - Substring (Objeto)", cod])

        v = round(float(row["VATRANSFERENCIA"]), 2) if pd.notna(row["VATRANSFERENCIA"]) else 0
        nome = row["nome_canonico_siggo"]
        data_cel = row["DACELEBRACAO_fmt"]
        ano_cel = row["ano_celebracao"]
        ano_vig = row["ano_vigencia"]
        # Nomes candidatos para as camadas de Beneficiário: tenta tanto o
        # nome de COBENEFICIADO quanto o da UG registrante (COUG) - em saúde/
        # educação é comum o SIGGO registrar a Secretaria como beneficiário
        # mas o Fundo (COUG) ser quem de fato aparece como recebedor na API.
        nomes_candidatos = [n for n in {nome, row["nome_canonico_coug"]} if pd.notna(n)]

        # 5) Soma NUORIGINAL "-NDx" (Termo de Adesão fracionado em 2+
        # registros no SIGGO - ver comentário acima de onde soma_grupo_nd é
        # calculado). Confere a soma do grupo, não o valor da linha isolada.
        if pd.notna(row["soma_grupo_nd"]):
            v_grupo = round(float(row["soma_grupo_nd"]), 2)
            for tabela, fonte in ((valor_para_parceria, "Gestão de Parcerias"),
                                  (valor_para_faf, "Fundo a Fundo"),
                                  (valor_para_esp, "Transferências Especiais")):
                if v_grupo in tabela.index:
                    ids = tabela.loc[v_grupo]
                    return pd.Series([fonte, "5 - Soma Substring (Nº Original)", " | ".join(map(str, ids))])

        # 6) Beneficiário + Data exata + Valor (só Fundo a Fundo tem data confiável)
        if v > 0 and data_cel:
            for nome_cand in nomes_candidatos:
                chave = (nome_cand, data_cel, v)
                if chave in benef_data_exata_valor_para_faf.index:
                    ids = benef_data_exata_valor_para_faf.loc[chave]
                    return pd.Series(["Fundo a Fundo", "6 - Beneficiário+Data+Valor", " | ".join(map(str, ids))])

        # 7) Beneficiário + Ano + Valor
        if v > 0:
            for ano in {ano_cel, ano_vig} - {None}:
                for nome_cand in nomes_candidatos:
                    for tabela, fonte in ((benef_ano_valor_para_parceria, "Gestão de Parcerias"),
                                          (benef_ano_valor_para_faf, "Fundo a Fundo"),
                                          (benef_ano_valor_para_esp, "Transferências Especiais")):
                        chave = (nome_cand, ano, v)
                        if chave in tabela.index:
                            ids = tabela.loc[chave]
                            return pd.Series([fonte, "7 - Beneficiário+Ano+Valor", " | ".join(map(str, ids))])

        # 8) Beneficiário + Valor (tenta tambem o nome pela UG registrante -
        # COUG - e, em Transferências Especiais, o Órgão Executor, que
        # costumam ser o beneficiário real em COBENEFICIADO - o beneficiário
        # formal da API é sempre o ente "DISTRITO FEDERAL")
        if v > 0:
            for nome_cand in nomes_candidatos:
                for tabela, fonte in ((benef_valor_para_parceria, "Gestão de Parcerias"),
                                      (benef_valor_para_faf, "Fundo a Fundo"),
                                      (benef_valor_para_esp, "Transferências Especiais"),
                                      (benef_valor_para_esp_executor, "Transferências Especiais")):
                    chave = (nome_cand, v)
                    if chave in tabela.index:
                        ids = tabela.loc[chave]
                        return pd.Series([fonte, "8 - Beneficiário+Valor", " | ".join(map(str, ids))])

        # 9) Beneficiário + Objeto (substring de texto, nao de numero) -
        # tenta tambem o nome pela UG registrante (COUG).
        achado_obj = None
        for nome_cand in nomes_candidatos:
            achado_obj = busca_objeto(row["objeto_upper"], nome_cand)
            if achado_obj:
                break
        if achado_obj:
            fonte, cod = achado_obj
            return pd.Series([fonte, "9 - Beneficiário+Objeto", cod])

        # Camada "Valor" (sem beneficiário) foi removida em 2026-09-17 por
        # decisao da usuaria: e uma chave fragil demais (so o valor bater,
        # sem nome nem data, gera muito falso positivo em valores redondos/
        # repetidos - ver memoria project_lacuna_saude_fundoafundo).
        return pd.Series(["Sem correspondência", "", ""])

    t["DACELEBRACAO_fmt"] = fmt_data(t["DACELEBRACAO"])
    t[["encontrado_em", "confianca", "identificador"]] = t.apply(cruzar, axis=1)

    nutransfsiafi_exibicao = pd.to_numeric(t["NUTRANSFSIAFI"], errors="coerce")
    encontrado_em_exibicao = t["encontrado_em"].replace("Sem correspondência", "-")
    saida = pd.DataFrame({
        "Nº Transf.": t["NUTRANSFERENCIA"],
        "Espécie": t["especie"],
        "Concedente": t["COCONCENTE"],
        "Beneficiário": t["beneficiario_fmt"],
        "Objeto": t["TXOBJETORESUMIDO"].astype(str).str.slice(0, 100),
        "Data Celeb.": fmt_data(t["DACELEBRACAO"]),
        "Valor (R$)": num(t["VATRANSFERENCIA"]),
        "Nº SICONV/SIAFI": nutransfsiafi_exibicao.where(nutransfsiafi_exibicao > 0, "").astype(str).replace("0.0", ""),
        "Nº Original": t["NUORIGINAL"],
        "Encontrado em": encontrado_em_exibicao,
        "Confiança": t["confianca"].replace("", "-"),
        "Indicador": t["identificador"],
    })

    # Cobertura por fonte: de cada identificador que EXISTE na fonte (ex: cada
    # Nº de Convênio do SICONV com proponente GDF), quantos foram de fato
    # localizados em algum registro do SIGGO (usado como "Indicador" em pelo
    # menos uma linha do cruzamento) - usado na aba Resumo. Compara por
    # digitos apenas (ignora "-"/"." etc.) porque alguns identificadores sao
    # salvos ora com traco (ex: "00905320250002-021705"), ora sem (quando vem
    # de busca por substring, que usa so digitos) - normaliza os dois lados.
    def _so_digitos_id(s):
        return re.sub(r"\D", "", str(s))

    universo_fontes = {
        "Discricionárias e Legais": nrs_siconv,
        "Gestão de Parcerias": set(gp_valido["cd_parceria_str"].dropna()),
        "Fundo a Fundo": set(faf["codigo_plano_acao"].dropna()),
        "Transferências Especiais": set(esp["codigo_plano_acao"].dropna()),
    }
    cobertura_fontes = {}
    for fonte, ids_fonte in universo_fontes.items():
        usados_dig = set()
        for val in saida.loc[saida["Encontrado em"] == fonte, "Indicador"].dropna():
            for parte in str(val).split(" | "):
                d = _so_digitos_id(parte)
                if d:
                    usados_dig.add(d)
        ids_originais_validos = {x for x in ids_fonte if _so_digitos_id(x)}
        nao_localizados_originais = {x for x in ids_originais_validos if _so_digitos_id(x) not in usados_dig}
        localizados = len(ids_originais_validos) - len(nao_localizados_originais)
        cobertura_fontes[fonte] = {
            "total_fonte": len(ids_originais_validos),
            "localizados": localizados,
            "nao_localizados": len(nao_localizados_originais),
            "ids_nao_localizados": nao_localizados_originais,
        }

    return saida.sort_values("Nº Transf.", ascending=False), params, cobertura_fontes


# ---------------------------------------------------------------------------
# 7) Resumo (achados do cruzamento SIGGO x TransfereGov)
# ---------------------------------------------------------------------------

ORDEM_CAMADAS = [
    "1 - Nº SICONV/SIAFI", "2 - Substring (Nº Original)", "3 - Substring (Conta Banc.)",
    "4 - Substring (Objeto)", "5 - Soma Substring (Nº Original)", "6 - Beneficiário+Data+Valor",
    "7 - Beneficiário+Ano+Valor", "8 - Beneficiário+Valor", "9 - Beneficiário+Objeto", "-",
]


# Para cada fonte, onde encontrar seus proprios registros (aba ja montada em
# main()) e quais colunas usar para juntar com a lista de "nao localizados"
# calculada em montar_cruzamento (id) e para decidir se vale a pena listar
# (valor pago/repassado - registros ainda nao celebrados ou com valor zerado
# ficam de fora, por serem fase inicial e nao representarem pendencia real).
FONTE_TAB_INFO = {
    "Discricionárias e Legais": {"aba": "siconv", "id_col": "Nº Convênio", "valor_col": "Valor pago (R$)"},
    "Gestão de Parcerias": {"aba": "parcerias", "id_col": "Nº Parceria", "valor_col": "Valor repassado (R$)"},
    "Fundo a Fundo": {"aba": "fundoafundo", "id_col": "Nº Plano de Ação", "valor_col": "Valor pago (R$)"},
    "Transferências Especiais": {"aba": "especiais", "id_col": "Nº Plano de Ação", "valor_col": "Valor pago (R$)"},
}


# Paleta fixa (nao ciclada) para as camadas de confianca - do mais robusto
# (teal escuro) ao mais fraco, "-" (sem correspondencia) em cinza neutro, sem
# tom de alarme (a ausencia de match nao é necessariamente um erro).
RAMPA_CAMADA = ["#00404a", "#00515e", "#006272", "#007386", "#00879c",
                "#0090a8", "#3aa8ba", "#6ec0cd", "#a3d8e0"]
COR_SEM_MATCH = "#c0392b"

# Paleta categorica fixa (mesma ordem sempre) para as 4 fontes do TransfereGov.
CORES_FONTE = {
    "Discricionárias e Legais": "#0d1b3e",
    "Gestão de Parcerias": "#0090a8",
    "Fundo a Fundo": "#f0a500",
    "Transferências Especiais": "#1a7a44",
    "-": COR_SEM_MATCH,
}


def _barra_resumo(label, largura_pct, texto_valor, cor):
    return f"""
      <div class="resumo-bar-row">
        <div class="resumo-bar-label" title="{label}">{label}</div>
        <div class="resumo-bar-track"><div class="resumo-bar-fill" style="width:{largura_pct:.1f}%;background:{cor}"></div></div>
        <div class="resumo-bar-value">{texto_valor}</div>
      </div>"""


def montar_resumo(cruzamento: pd.DataFrame, cobertura_fontes: dict, dados_fontes: dict):
    total = len(cruzamento)
    com_match = int((cruzamento["Encontrado em"] != "-").sum())
    sem_match = total - com_match
    valor_total = float(cruzamento["Valor (R$)"].sum())
    valor_com_match = float(cruzamento.loc[cruzamento["Encontrado em"] != "-", "Valor (R$)"].sum())
    taxa = (com_match / total * 100) if total else 0
    taxa_valor = (valor_com_match / valor_total * 100) if valor_total else 0

    def fmt_int(n):
        return f"{n:,}".replace(",", ".")

    taxa_fmt = f"{taxa:.1f}".replace(".", ",")
    taxa_valor_fmt = f"{taxa_valor:.1f}".replace(".", ",")
    kpis_html = f"""
    <div class="krow">
      <div class="kpi"><div class="kl">Registros no SIGGO (aba Cruzamento)</div><div class="kv">{fmt_int(total)}</div></div>
      <div class="kpi ko"><div class="kl">Com correspondência no TransfereGov</div><div class="kv">{fmt_int(com_match)} ({taxa_fmt}%)</div></div>
      <div class="kpi ka"><div class="kl">Sem correspondência</div><div class="kv">{fmt_int(sem_match)}</div></div>
      <div class="kpi"><div class="kl">Valor total (R$)</div><div class="kv">{fmt_valor(valor_total)}</div></div>
      <div class="kpi ko"><div class="kl">Valor com correspondência</div><div class="kv">{fmt_valor(valor_com_match)} ({taxa_valor_fmt}%)</div></div>
    </div>
    """

    # --- Tabela 1: por camada de confianca (ordem fixa - do mais robusto ao
    # sem correspondencia) ---
    grp_cam = cruzamento.groupby("Confiança").agg(
        Registros=("Nº Transf.", "count"), Valor=("Valor (R$)", "sum")
    )
    ordem_presente = [c for c in ORDEM_CAMADAS if c in grp_cam.index]
    grp_cam = grp_cam.loc[ordem_presente].reset_index().rename(
        columns={"Confiança": "Camada", "Valor": "Valor total (R$)"}
    )
    grp_cam["% dos registros"] = (
        (grp_cam["Registros"] / total * 100).round(1).astype(str).str.replace(".", ",", regex=False) + "%"
        if total else ""
    )

    # --- Tabela 2: por fonte (Encontrado em) ---
    grp_fonte = cruzamento.groupby("Encontrado em").agg(
        Registros=("Nº Transf.", "count"), Valor=("Valor (R$)", "sum")
    ).reset_index().rename(columns={"Encontrado em": "Fonte", "Valor": "Valor total (R$)"})
    grp_fonte["% dos registros"] = (
        (grp_fonte["Registros"] / total * 100).round(1).astype(str).str.replace(".", ",", regex=False) + "%"
        if total else ""
    )
    grp_fonte["_ordem"] = grp_fonte["Fonte"] == "-"
    grp_fonte = grp_fonte.sort_values(["_ordem", "Registros"], ascending=[True, False]).drop(columns="_ordem")

    # --- Widget 1: por camada de confianca, como barras horizontais (a soma
    # das barras da 100% dos registros - inclui "-") ---
    barras_camada = "".join(
        _barra_resumo(
            row["Camada"],
            (row["Registros"] / total * 100) if total else 0,
            f'{fmt_int(row["Registros"])} · {fmt_valor(row["Valor total (R$)"])}',
            COR_SEM_MATCH if row["Camada"] == "-" else RAMPA_CAMADA[min(int(row["Camada"][0]) - 1, len(RAMPA_CAMADA) - 1)],
        )
        for _, row in grp_cam.iterrows()
    )

    # --- Widget 2: por fonte, como barras horizontais (paleta categorica fixa) ---
    barras_fonte = "".join(
        _barra_resumo(
            row["Fonte"], (row["Registros"] / total * 100) if total else 0,
            f'{fmt_int(row["Registros"])} · {fmt_valor(row["Valor total (R$)"])}',
            CORES_FONTE.get(row["Fonte"], "#8a97ad"),
        )
        for _, row in grp_fonte.iterrows()
    )

    # --- Widget 3: cobertura por fonte, como cards com barra de progresso
    # (status: verde >=50% localizado, amber 25-49%, vermelho <25%) ---
    def _cor_cobertura(pct):
        if pct >= 50:
            return "#1a7a44"
        if pct >= 25:
            return "#f0a500"
        return "#c0392b"

    cards_cobertura = "".join(f"""
      <div class="resumo-cov-card">
        <div class="kl">{fonte}</div>
        <div class="resumo-cov-pct" style="color:{_cor_cobertura(c["localizados"] / c["total_fonte"] * 100 if c["total_fonte"] else 0)}">
          {(c["localizados"] / c["total_fonte"] * 100 if c["total_fonte"] else 0):.0f}%
        </div>
        <div class="resumo-cov-track"><div class="resumo-cov-fill" style="width:{(c["localizados"] / c["total_fonte"] * 100 if c["total_fonte"] else 0):.1f}%;background:{_cor_cobertura(c["localizados"] / c["total_fonte"] * 100 if c["total_fonte"] else 0)}"></div></div>
        <div class="resumo-cov-sub">{fmt_int(c["localizados"])} de {fmt_int(c["total_fonte"])} localizados no SIGGO</div>
      </div>"""
        for fonte, c in cobertura_fontes.items()
    )

    # --- Tabela 4: pendentes no TransfereGov - visao inversa (o que consta na
    # API e NAO foi localizado no SIGGO). So lista quem ja tem valor pago ou
    # repassado (>0): quem ainda nao foi celebrado, ou foi celebrado mas com
    # valor zerado, esta em fase inicial e nao representa pendencia real de
    # regularizacao no SIGGO.
    def _so_digitos_id(s):
        return re.sub(r"\D", "", str(s))

    linhas_pendentes = []
    for fonte, info in FONTE_TAB_INFO.items():
        ids_nao_loc_dig = {_so_digitos_id(x) for x in cobertura_fontes.get(fonte, {}).get("ids_nao_localizados", set())}
        if not ids_nao_loc_dig:
            continue
        df_fonte = dados_fontes.get(info["aba"])
        if df_fonte is None or df_fonte.empty:
            continue
        id_dig = df_fonte[info["id_col"]].apply(_so_digitos_id)
        mascara = id_dig.isin(ids_nao_loc_dig) & (num(df_fonte[info["valor_col"]]) > 0)
        sub = df_fonte.loc[mascara].copy()
        if sub.empty:
            continue
        linhas_pendentes.append(pd.DataFrame({
            "Fonte": fonte,
            "Identificador": sub[info["id_col"]],
            "Beneficiário": sub.get("Beneficiário", ""),
            "Data de celebração": sub.get("Data de celebração", ""),
            "Valor (R$)": num(sub[info["valor_col"]]),
        }))
    df_pendentes = (
        pd.concat(linhas_pendentes, ignore_index=True).sort_values("Valor (R$)", ascending=False)
        if linhas_pendentes else
        pd.DataFrame(columns=["Fonte", "Identificador", "Beneficiário", "Data de celebração", "Valor (R$)"])
    )
    params_pendentes = {
        "Valor (R$)": "valor pago/repassado já executado (> 0) na fonte - exclui o que ainda não foi celebrado ou está zerado",
    }

    # --- Tabela 5: por UG beneficiaria - quais UGs do GDF mais precisam de
    # regularizacao no SIGGO (mais registros sem correspondencia/maior valor
    # sem correspondencia) ---
    cz = cruzamento.copy()
    cz["_sem_match"] = cz["Encontrado em"] == "-"
    grp_ug = cz.groupby("Beneficiário").agg(
        Registros=("Nº Transf.", "count"),
        **{"Sem correspondência": ("_sem_match", "sum")},
        **{"Valor total (R$)": ("Valor (R$)", "sum")},
    )
    valor_sem_match = cz.loc[cz["_sem_match"]].groupby("Beneficiário")["Valor (R$)"].sum()
    grp_ug["Valor s/ corresp. (R$)"] = grp_ug.index.map(valor_sem_match).fillna(0)
    grp_ug = grp_ug.reset_index().rename(columns={"Beneficiário": "UG"})
    grp_ug["% s/ corresp."] = (
        (grp_ug["Sem correspondência"] / grp_ug["Registros"] * 100).round(1).astype(str).str.replace(".", ",", regex=False) + "%"
    )
    grp_ug = grp_ug.sort_values("Valor s/ corresp. (R$)", ascending=False)
    grp_ug = grp_ug[grp_ug["Sem correspondência"] > 0]
    params_ug = {
        "Sem correspondência": "registros dessa UG na aba Cruzamento sem nenhuma correspondência localizada",
    }

    html = f"""
    <p class="fonte-info">Visão consolidada dos achados da aba Cruzamento - quantos registros do SIGGO foram
    localizados em cada fonte do TransfereGov, por qual camada de confiança, quanto de cada fonte (SICONV,
    Gestão de Parcerias, Fundo a Fundo, Transferências Especiais) já foi localizado no SIGGO, o que consta na
    fonte mas ainda não foi localizado, e quais UGs do GDF mais precisam de regularização.</p>
    {kpis_html}
    <div class="resumo-widgets">
      <div class="resumo-widget-card">
        <h3 class="resumo-titulo">Por camada de confiança</h3>
        <div class="resumo-bars">{barras_camada}</div>
      </div>
      <div class="resumo-widget-card">
        <h3 class="resumo-titulo">Por fonte (Encontrado em)</h3>
        <div class="resumo-bars">{barras_fonte}</div>
      </div>
    </div>
    <h3 class="resumo-titulo">Cobertura por fonte — o que já foi localizado no SIGGO</h3>
    <div class="resumo-cov-grid">{cards_cobertura}</div>
    <div class="resumo-grid">
      <div>
        <h3 class="resumo-titulo">Por UG beneficiária — quem mais precisa de regularização no SIGGO</h3>
        {montar_tabela_html(grp_ug, "resumo_ug", params_ug)}
      </div>
      <div>
        <h3 class="resumo-titulo">Pendentes no TransfereGov — consta na API, valor já executado, mas não foi localizado no SIGGO</h3>
        {montar_tabela_html(df_pendentes, "resumo_pendentes", params_pendentes)}
      </div>
    </div>
    """
    return html


# ---------------------------------------------------------------------------
# Montagem do HTML
# ---------------------------------------------------------------------------

ABA_INICIAL = "resumo"

ABAS = [
    {"id": "parcerias", "titulo": "Gestão de Parcerias", "icone": "🤝",
     "fonte": "API /parcerias — endpoints /proposta + /parceria + /programa + /documento-habil + /ordem-pagamento",
     "coluna_ente": "Beneficiário", "coluna_valor_kpi": "Valor repassado (R$)"},
    {"id": "especiais", "titulo": "Transferências Especiais", "icone": "🏛️",
     "fonte": "API /especiais — endpoints /beneficiarios-especiais + /planos-acao-especiais + /programas-especiais + cadeia empenho→documento hábil→ordem de pagamento",
     "coluna_ente": "Beneficiário", "coluna_valor_kpi": "Valor pago (R$)"},
    {"id": "fundoafundo", "titulo": "Fundo a Fundo", "icone": "💰",
     "fonte": "API /fundoafundo — endpoint /planos-acao + cadeia dados bancários→lançamentos→subtransações",
     "coluna_ente": "Beneficiário", "coluna_valor_kpi": "Valor pago (R$)"},
    {"id": "siconv", "titulo": "Discricionárias e Legais", "icone": "📄",
     "fonte": "Download CSV \"Transferências Discricionárias e Legais\" (Transferegov) — siconv_convenio + siconv_proposta + siconv_emenda: convênios, contratos de repasse e termos de compromisso",
     "coluna_ente": "Beneficiário", "coluna_valor_kpi": "Valor pago (R$)"},
    {"id": "siggo", "titulo": "SIGGO", "icone": "🗄️",
     "fonte": "Oracle SIGGO — MIL2026.TRANSFERENCIA + MIL2026.UNIDADEGESTORA (ainda não cruzado com o TransfereGov)",
     "coluna_ente": "Beneficiário", "coluna_valor_kpi": "Valor transferência (R$)"},
    {"id": "cruzamento", "titulo": "Cruzamento", "icone": "🔗",
     "fonte": "SIGGO (MIL2026.TRANSFERENCIA) cruzado com as 4 fontes do TransfereGov — nº exato (SICONV) ou valor (demais, heurística)",
     "coluna_ente": "Beneficiário", "coluna_valor_kpi": "Valor (R$)"},
    {"id": "resumo", "titulo": "Resumo", "icone": "📊",
     "fonte": "Consolidado a partir da aba Cruzamento",
     "coluna_ente": None, "coluna_valor_kpi": None},
]


def fmt_valor(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return f"{v:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")


def montar_filtros(df: pd.DataFrame, aba_id: str) -> str:
    anos = sorted([int(a) for a in df["Ano"].dropna().unique()], reverse=True) if "Ano" in df.columns else []
    situacoes = sorted([s for s in df["Situação"].dropna().unique()]) if "Situação" in df.columns else []
    modalidades = sorted([m for m in df["Modalidade"].dropna().unique() if m]) if "Modalidade" in df.columns else []

    opts_ano = "".join(f'<option value="{a}">{a}</option>' for a in anos)
    opts_sit = "".join(f'<option value="{s}">{s}</option>' for s in situacoes)
    opts_mod = "".join(f'<option value="{m}">{m}</option>' for m in modalidades)

    campo_modalidade = ""
    if modalidades:
        campo_modalidade = f"""
      <div class="fg">
        <label>Modalidade</label>
        <select id="fm-{aba_id}" onchange="aplicarFiltros('{aba_id}')"><option value="">Todas</option>{opts_mod}</select>
      </div>
        """

    return f"""
    <div class="fbar">
      <div class="fg">
        <label>Beneficiário</label>
        <input type="text" id="fe-{aba_id}" placeholder="Buscar por nome…" oninput="aplicarFiltros('{aba_id}')">
      </div>
      <div class="fg">
        <label>Ano</label>
        <select id="fa-{aba_id}" onchange="aplicarFiltros('{aba_id}')"><option value="">Todos</option>{opts_ano}</select>
      </div>
      <div class="fg">
        <label>Situação</label>
        <select id="fs-{aba_id}" onchange="aplicarFiltros('{aba_id}')"><option value="">Todas</option>{opts_sit}</select>
      </div>
      {campo_modalidade}
      <div class="fg">
        <label>Busca livre</label>
        <input type="text" id="fl-{aba_id}" placeholder="Qualquer campo…" oninput="aplicarFiltros('{aba_id}')">
      </div>
      <div class="bgrp">
        <button class="btn btn-g" onclick="limparFiltros('{aba_id}')">↺ Limpar filtros</button>
        <span class="contador" id="contador-{aba_id}">{len(df)} registros</span>
      </div>
    </div>
    """


def montar_filtros_siggo(df: pd.DataFrame, aba_id: str) -> str:
    especies = sorted(e for e in df["Espécie"].dropna().unique() if e)
    opts_especie = "".join(f'<option value="{e}">{e}</option>' for e in especies)

    return f"""
    <div class="fbar">
      <div class="fg">
        <label>Espécie</label>
        <select id="fesp-{aba_id}" onchange="aplicarFiltrosSiggo()"><option value="">Todas</option>{opts_especie}</select>
      </div>
      <div class="fg">
        <label>Nº Transferência</label>
        <input type="text" id="fnt-{aba_id}" placeholder="Nº transferência…" oninput="aplicarFiltrosSiggo()">
      </div>
      <div class="fg">
        <label>Concedente</label>
        <input type="text" id="fcon-{aba_id}" placeholder="CNPJ do concedente…" oninput="aplicarFiltrosSiggo()">
      </div>
      <div class="fg">
        <label>Beneficiário</label>
        <input type="text" id="fben-{aba_id}" placeholder="Nome ou código da UG…" oninput="aplicarFiltrosSiggo()">
      </div>
      <div class="fg">
        <label>Nº SICONV/SIAFI</label>
        <input type="text" id="fsia-{aba_id}" placeholder="Nº SICONV/SIAFI…" oninput="aplicarFiltrosSiggo()">
      </div>
      <div class="fg">
        <label>Busca livre</label>
        <input type="text" id="fl-{aba_id}" placeholder="Qualquer campo…" oninput="aplicarFiltrosSiggo()">
      </div>
      <div class="bgrp">
        <button class="btn btn-g" onclick="limparFiltrosSiggo()">↺ Limpar filtros</button>
        <span class="contador" id="contador-{aba_id}">{len(df)} registros</span>
      </div>
    </div>
    """


def montar_filtros_cruzamento(df: pd.DataFrame, aba_id: str) -> str:
    especies = sorted(e for e in df["Espécie"].dropna().unique() if e)
    fontes = sorted(e for e in df["Encontrado em"].dropna().unique() if e)
    opts_especie = "".join(f'<option value="{e}">{e}</option>' for e in especies)
    opts_fonte = "".join(f'<option value="{e}">{e}</option>' for e in fontes)

    return f"""
    <div class="fbar">
      <div class="fg">
        <label>Espécie</label>
        <select id="fesp-{aba_id}" onchange="aplicarFiltrosCruzamento()"><option value="">Todas</option>{opts_especie}</select>
      </div>
      <div class="fg">
        <label>Beneficiário</label>
        <input type="text" id="fben-{aba_id}" placeholder="Nome ou código da UG…" oninput="aplicarFiltrosCruzamento()">
      </div>
      <div class="fg">
        <label>Encontrado em</label>
        <select id="ffonte-{aba_id}" onchange="aplicarFiltrosCruzamento()"><option value="">Todas</option>{opts_fonte}</select>
      </div>
      <div class="fg">
        <label>Busca livre</label>
        <input type="text" id="fl-{aba_id}" placeholder="Qualquer campo…" oninput="aplicarFiltrosCruzamento()">
      </div>
      <div class="bgrp">
        <button class="btn btn-g" onclick="limparFiltrosCruzamento()">↺ Limpar filtros</button>
        <span class="contador" id="contador-{aba_id}">{len(df)} registros</span>
      </div>
    </div>
    """


# Larguras reduzidas para colunas especificas por aba (rotulo curto + coluna
# estreita), usado na aba Cruzamento por ter muitas colunas e pouco espaco
# horizontal.
LARGURAS_COLUNA = {
    "cruzamento": {
        "Nº Transf.": "78px",
        "Valor (R$)": "110px",
        "Data Celeb.": "88px",
        "Beneficiário": "150px",
        "Objeto": "150px",
    },
}


def montar_tabela_html(df: pd.DataFrame, aba_id: str, params: dict) -> str:
    colunas = list(df.columns)
    registros = df.fillna("").to_dict(orient="records")
    for r in registros:
        for c in colunas:
            if "Valor" in c or "valor" in c:
                r[c] = fmt_valor(r[c]) if r[c] != "" else ""

    def cabecalho(c, i):
        classe = "num" if ("Valor" in c or "valor" in c) else ""
        param = params.get(c, "")
        sub = f'<span class="acct">{param}</span>' if param else ""
        largura = LARGURAS_COLUNA.get(aba_id, {}).get(c)
        estilo = f' style="width:{largura}"' if largura else ""
        return (
            f'<th class="{classe}"{estilo} onclick="ordenar(\'{aba_id}\',{i})">{c} '
            f'<span id="si-{aba_id}-{i}" class="si">⇅</span>{sub}</th>'
        )

    thead = "".join(cabecalho(c, i) for i, c in enumerate(colunas))
    dados_json = json.dumps(registros, ensure_ascii=False)

    return f"""
    <div class="tsec">
      <div class="tw">
        <table id="tabela-{aba_id}">
          <thead><tr>{thead}</tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
    <script>
      DADOS['{aba_id}'] = {dados_json};
      COLUNAS['{aba_id}'] = {json.dumps(colunas, ensure_ascii=False)};
      COLUNAS_NUM['{aba_id}'] = {json.dumps(["Valor" in c or "valor" in c for c in colunas])};
    </script>
    """


ABA_PARA_FONTE = {"parcerias": "Gestão de Parcerias", "especiais": "Transferências Especiais",
                  "fundoafundo": "Fundo a Fundo", "siconv": "Discricionárias e Legais"}


def carregar_datas_atualizacao() -> dict:
    """Ultima atualizacao publicada por cada fonte de origem (gerada em
    extrair_transferegov_df.py) - mostra a idade dos dados no painel."""
    try:
        df = carregar("df_data_atualizacao_fontes.csv", dtype=str)
    except FileNotFoundError:
        return {}
    out = {}
    for _, r in df.iterrows():
        dt = pd.to_datetime(r["atualizado_em"], dayfirst="/" in str(r["atualizado_em"]), errors="coerce")
        if pd.notna(dt):
            out[r["fonte"]] = dt.strftime("%d/%m/%Y %H:%M") if (dt.hour or dt.minute) else dt.strftime("%d/%m/%Y")
    return out


def main():
    datas_fonte = carregar_datas_atualizacao()
    cruzamento_df, cruzamento_params, cobertura_fontes = montar_cruzamento()
    resultados = {
        "parcerias": montar_parcerias(),
        "especiais": montar_especiais(),
        "fundoafundo": montar_fundoafundo(),
        "siconv": montar_siconv(),
        "siggo": montar_siggo(),
        "cruzamento": (cruzamento_df, cruzamento_params),
    }
    dados = {aid: df for aid, (df, _) in resultados.items()}
    parametros = {aid: params for aid, (_, params) in resultados.items()}
    resumo_html = montar_resumo(cruzamento_df, cobertura_fontes, dados)

    abas_html = []
    conteudo_html = []
    for i, aba in enumerate(ABAS):
        aid = aba["id"]
        if aid == "resumo":
            ativo = "ativo" if aid == ABA_INICIAL else ""
            abas_html.append(
                f'<button class="aba-btn {ativo}" data-aba="{aid}" onclick="mostrarAba(\'{aid}\')">'
                f'{aba["icone"]} {aba["titulo"]}</button>'
            )
            conteudo_html.append(f"""
            <section class="aba-conteudo {ativo}" id="conteudo-{aid}">
              {resumo_html}
            </section>
            """)
            continue

        df = dados[aid]
        ativo = "ativo" if aid == ABA_INICIAL else ""
        abas_html.append(
            f'<button class="aba-btn {ativo}" data-aba="{aid}" onclick="mostrarAba(\'{aid}\')">'
            f'{aba["icone"]} {aba["titulo"]} <span class="badge-count">{len(df)}</span></button>'
        )

        if aid == "siggo":
            filtros_html = montar_filtros_siggo(df, aid)
        elif aid == "cruzamento":
            filtros_html = montar_filtros_cruzamento(df, aid)
        else:
            filtros_html = montar_filtros(df, aid)
        tabela_html = montar_tabela_html(df, aid, parametros[aid])
        dt_fonte = datas_fonte.get(ABA_PARA_FONTE.get(aid, ""))
        atualizado = f" — dados de origem atualizados em {dt_fonte}" if dt_fonte else ""

        conteudo_html.append(f"""
        <section class="aba-conteudo {ativo}" id="conteudo-{aid}">
          <p class="fonte-info">Fonte: {aba['fonte']}{atualizado}</p>
          {filtros_html}
          {tabela_html}
        </section>
        """)

    html = TEMPLATE.format(
        abas_nav="".join(abas_html),
        abas_conteudo="".join(conteudo_html),
        data_geracao=pd.Timestamp.now().strftime("%d/%m/%Y %H:%M"),
        aba_inicial=ABA_INICIAL,
    )

    saida = PASTA / "transferegov.html"
    saida.write_text(html, encoding="utf-8")
    print(f"Painel salvo em {saida}")
    for aid, df in dados.items():
        rotulo = "filtrado por papel: concedente != GDF e beneficiario = GDF" if aid == "siggo" else "somente GDF"
        print(f"  {aid}: {len(df)} registros ({rotulo})")


TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TransfereGov × GDF — Visão Consolidada</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --navy:#0d1b3e;--navy-mid:#162550;--navy-light:#1e3267;
  --teal:#0090a8;--teal-light:#00b8d4;
  --surface:#fff;--bg:#f2f5f9;--border:#dce3ed;
  --row-alt:#f4f7fb;--hover:#e8f0f8;
  --text:#1a2033;--muted:#6b7a99;
  --red:#c0392b;--green:#1a7a44;--radius:10px;
  --shadow:0 2px 12px rgba(13,27,62,.10);
}}
body{{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}}
header{{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}}
.hlogo{{width:32px;height:32px;background:var(--teal);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}}
header h1{{font-size:14px;font-weight:700;letter-spacing:.6px}}
header h1 span{{font-weight:400;color:#9ab0cc;font-size:12px;display:block;letter-spacing:0;margin-top:1px}}
.voltar{{font-size:11px;color:#7a99bb;text-decoration:none;display:flex;align-items:center;gap:4px;margin-left:20px;opacity:.8}}
.voltar:hover{{opacity:1}}
#ts{{font-size:11px;color:#7a99bb;white-space:nowrap}}

.abas-nav{{background:var(--surface);border-bottom:1px solid var(--border);padding:0 28px;display:flex;gap:4px;overflow-x:auto}}
.aba-btn{{background:none;border:none;padding:14px 18px;font-size:12.5px;font-weight:600;color:var(--muted);cursor:pointer;border-bottom:3px solid transparent;white-space:nowrap;transition:.15s}}
.aba-btn:hover{{color:var(--navy)}}
.aba-btn.ativo{{color:var(--teal);border-bottom-color:var(--teal)}}
.badge-count{{background:var(--bg);color:var(--muted);border-radius:20px;padding:1px 8px;font-size:10.5px;margin-left:4px}}
.aba-btn.ativo .badge-count{{background:var(--teal);color:#fff}}

.aba-conteudo{{display:none}}
.aba-conteudo.ativo{{display:block}}
.fonte-info{{font-size:11px;color:var(--muted);margin:14px 28px 0;background:#fff;border:1px solid var(--border);border-radius:6px;padding:7px 12px;display:inline-block}}
.resumo-titulo{{font-size:13px;font-weight:700;color:var(--navy);margin:22px 28px 8px}}
.resumo-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:20px;padding:0 28px}}
.resumo-grid .resumo-titulo{{margin:0 0 8px}}
.resumo-grid .tsec{{padding:0}}
.resumo-grid .tw{{max-height:420px}}
.resumo-widgets{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:20px;padding:8px 28px 0}}
.resumo-widget-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow)}}
.resumo-widget-card .resumo-titulo{{margin:0 0 12px}}
.resumo-bars{{display:flex;flex-direction:column;gap:9px}}
.resumo-bar-row{{display:grid;grid-template-columns:180px 1fr auto;align-items:center;gap:10px}}
.resumo-bar-label{{font-size:11.5px;font-weight:600;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.resumo-bar-track{{background:var(--row-alt);border-radius:5px;height:14px;overflow:hidden}}
.resumo-bar-fill{{height:100%;border-radius:5px;min-width:2px}}
.resumo-bar-value{{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}}
.resumo-cov-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;padding:0 28px 8px}}
.resumo-cov-card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow)}}
.resumo-cov-pct{{font-size:26px;font-weight:700;letter-spacing:-.5px;margin:4px 0}}
.resumo-cov-track{{background:var(--row-alt);border-radius:5px;height:7px;overflow:hidden;margin-bottom:6px}}
.resumo-cov-fill{{height:100%;border-radius:5px}}
.resumo-cov-sub{{font-size:10.5px;color:var(--muted)}}

.krow{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;padding:14px 28px 4px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}}
.kpi::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}}
.kpi.kw::before{{background:linear-gradient(90deg,#f0a500,#ffcc44)}}
.kpi.ka::before{{background:linear-gradient(90deg,var(--red),#e74c3c)}}
.kpi.ko::before{{background:linear-gradient(90deg,var(--green),#27ae60)}}
.kl{{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.kv{{font-size:19px;font-weight:700;letter-spacing:-.3px;line-height:1}}

.fbar{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);margin:14px 28px 0;box-shadow:var(--shadow);padding:14px 18px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}}
.fg{{display:flex;flex-direction:column;gap:4px}}
.fg label{{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}}
.fg select,.fg input[type=text]{{border:1.5px solid var(--border);border-radius:6px;padding:7px 10px;font-size:12.5px;background:#fff;color:var(--text);min-width:170px}}
.fg select{{padding-right:28px;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 9px center;appearance:none}}
.fg select:focus,.fg input[type=text]:focus{{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}}
.bgrp{{display:flex;gap:10px;margin-left:auto;align-items:center;flex-wrap:wrap}}
.btn{{display:inline-flex;align-items:center;gap:5px;padding:7px 14px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;transition:filter .15s,transform .1s;white-space:nowrap}}
.btn:hover{{filter:brightness(1.08);transform:translateY(-1px)}}
.btn-g{{background:var(--border);color:var(--text)}}
.contador{{font-size:11.5px;color:var(--muted);white-space:nowrap}}

.tsec{{padding:16px 28px 32px}}
.tw{{border-radius:var(--radius);border:1px solid var(--border);overflow:auto;max-height:65vh;box-shadow:var(--shadow)}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
thead{{position:sticky;top:0;background:var(--navy);color:#c8d8ec;z-index:5}}
th{{padding:10px 12px;text-align:left;font-weight:600;white-space:nowrap;cursor:pointer;user-select:none;letter-spacing:.2px;max-width:180px}}
.acct{{display:block;font-size:9px;font-weight:400;color:#9ab0cc;letter-spacing:.2px;margin-top:3px;white-space:normal;overflow-wrap:anywhere;text-transform:none;max-width:160px}}
th:hover{{background:var(--navy-light)}}
td{{padding:7px 12px;border-bottom:1px solid var(--border);white-space:nowrap;max-width:240px;overflow:hidden;text-overflow:ellipsis;font-size:12px}}
th.num,td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.si{{display:inline-block;width:12px;text-align:center;opacity:.55;font-size:10px}}
tbody tr:nth-child(even){{background:var(--row-alt)}}
tbody tr:hover td{{background:var(--hover)}}

.rodape-nota{{margin:0 28px 28px;font-size:11px;color:var(--muted);background:#fff;border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px}}
.rodape-nota code{{background:var(--bg);padding:1px 5px;border-radius:4px;font-size:10.5px}}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">🤝</div>
    <h1>TRANSFEREGOV<span>Visão consolidada — somente órgãos/entidades do Distrito Federal</span></h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <span id="ts">Gerado em {data_geracao}</span>
</header>

<nav class="abas-nav">
  {abas_nav}
</nav>

<script>
window.DADOS = {{}};
window.COLUNAS = {{}};
window.COLUNAS_NUM = {{}};
window.ORDEM = {{}};
window.LINHAS_ATUAIS = {{}};
</script>

{abas_conteudo}

<div class="rodape-nota">
  <strong>Escopo:</strong> este painel mostra <strong>somente</strong> registros cujo CNPJ do ente beneficiário/proponente
  bate com a lista de órgãos e entidades do GDF (extraída de <code>MIL2026.UNIDADEGESTORA</code>, campo <code>NUCGC</code>,
  mais o CNPJ do ente federativo Distrito Federal 00.394.601/0001-26). Organizações privadas (associações, cooperativas,
  empresas) apenas sediadas em Brasília foram excluídas por esse filtro. Essa regra continua necessária mesmo após adotar
  os critérios do setor de Transparência/Governo Aberto do DF: testamos e, no SICONV, sem o filtro de CNPJ restariam 5.443
  propostas com UF=DF (só excluindo Termo de Fomento), das quais apenas 1.711 são de fato do GDF — as outras ~3.700 são de
  terceiros privados sediados em Brasília. Traz <strong>todos os recursos, com ou sem emenda parlamentar</strong> (diferente
  da consulta pública de Emendas Parlamentares Federais do Portal da Transparência DF, que filtra só o que tem emenda).
  Registros de <strong>Termo de Fomento</strong> são excluídos explicitamente no SICONV (via campo Modalidade), embora o
  filtro de CNPJ já os elimine na prática (é um instrumento exclusivo com OSCs). Campo <strong>Valor pago</strong> segue as
  regras de cálculo usadas pelo setor de Transparência/Governo Aberto do DF (cadeia de tabelas por fonte). Fonte dos dados:
  <code>https://api-publica.transferegov.gestao.gov.br/</code>.<br><br>
  <strong>Aba Discricionárias e Legais:</strong> vem dos CSVs "Transferências Discricionárias e Legais" do Transferegov
  (<code>siconv_convenio</code>, <code>siconv_proposta</code> e <code>siconv_emenda</code>), que reúnem convênios, contratos de
  repasse e termos de compromisso — não apenas o SICONV legado. Dos 65 arquivos disponibilizados, os demais trazem detalhamentos
  (desembolsos, aditivos, licitações, obras etc.) sem novos instrumentos, conferido em 2026-09-18. Foram incluídas, por CNPJ
  complementar, a Secretaria de Turismo e a Secretaria Especial da Promoção da Igualdade Racial do DF (que o SICONV cadastra
  com CNPJ diferente do constante no SIGGo), além da Empresa Brasiliense de Turismo e da Caesb, empresas distritais que não são
  UG do SIGGo mas firmaram instrumentos com a União e devem constar para fins de transparência e controle, ainda que extintas.<br><br>
  <strong>Aba SIGGO:</strong> de 18.624 registros de <code>MIL2026.TRANSFERENCIA</code>, mostra só os <strong>1.823</strong> em que
  o concedente NÃO é o GDF e o beneficiário É o GDF — ou seja, recurso externo (essencialmente da União) recebido pelo GDF, excluindo
  os 16.512 casos em que o próprio GDF é concedente (a órgãos privados/OSCs ou a si mesmo), os 88 casos intra-GDF, e 105 casos em que o
  concedente, embora não seja GDF, também não é a União — empresas distritais (Terracap, Caesb, BRB), organismos internacionais,
  fundações/associações privadas, outro município, empréstimos internacionais tomados pelo próprio GDF, e 2 códigos de UG legados
  ("150105-00001"/"090101-00001") cujo objeto é claramente do próprio GDF (ver <code>CONCEDENTES_NAO_UNIAO</code> no gerador;
  verificado CNPJ a CNPJ na Receita Federal em 2026-09-17 — mantidos no escopo bancos/empresas 100% federais que atuam
  como agente financeiro de repasses da União, como Caixa, Banco do Brasil, BNDES, Correios, Embrapa, FINEP e CBTU). O papel é
  reconhecido tanto em CNPJ quanto no formato "UG-sequencial" usado por <code>COCONCENTE</code>/<code>COBENEFICIADO</code>. Filtro por
  papel, não por espécie — testado e mais preciso (6 espécies como Auxílio/Subvenção/PDAF nunca aparecem como "União→GDF" na base
  inteira, mas Termo de Fomento tem alguns casos raros e legítimos que uma lista de espécies excluiria por engano). Esta aba ainda não
  foi cruzada com as demais (TransfereGov); serve para validação e exploração antes do cruzamento.<br><br>
  <strong>Aba Cruzamento:</strong> para cada um dos registros do SIGGO, tenta achar o instrumento
  correspondente no TransfereGov, sempre pela chave <strong>mais robusta disponível primeiro</strong>, em camadas de confiança
  decrescente: <strong>1 — Nº SICONV/SIAFI:</strong> <code>NUTRANSFSIAFI</code> = <code>NR_CONVENIO</code> no SICONV.
  <strong>2 — Substring (Nº Original):</strong> os dígitos de <code>NUORIGINAL</code> contêm (ou estão contidos em) o
  identificador de qualquer fonte (<code>NR_CONVENIO</code>/<code>cd_parceria</code>/<code>codigo_plano_acao</code>/
  <code>numero_emenda_parlamentar_plano_acao</code> de Transferências Especiais ou SICONV — incluído em 2026-09-17 a partir de um
  caso real encontrado pela usuária: NUORIGINAL "202443780013" batia com o Nº da Emenda Parlamentar, não com o código do
  plano de ação), exigindo 6+ dígitos em comum. <strong>3 — Substring (Conta Banc.):</strong> os dígitos de <code>NUCONTA</code> contêm/estão contidos na
  conta bancária cadastrada na fonte (inclui, no Fundo a Fundo, tanto a conta de <code>planos-acao-dados-bancarios</code> quanto a de
  <code>gestao-financeira-lancamentos</code>) — exige 4+ dígitos em comum. Para Discricionárias e Legais compara a conta do proponente no SICONV
  (<code>CD_CONTA</code>, de <code>siconv_proposta</code>) por <strong>igualdade exata</strong> (testado: por substring só 51% dos casos eram
  coerentes em valor/ano; por igualdade exata, 99%). Só vale quando a conta é usada por no máximo 5 registros do SIGGo — contas
  compartilhadas, como a conta única do Fundo de Saúde, não identificam um instrumento (sem essa trava, um único convênio "absorvia" 36
  registros de Saúde de anos diferentes). <strong>4 — Substring (Objeto):</strong> procura, no texto do
  <code>TXOBJETORESUMIDO</code>, um número citado logo após palavras-chave como "SICONV", "Convênio", "Parceria" ou "Emenda" (ex: "SICONV
  Nº 825427/2015") e testa esse número contra os identificadores das fontes — não extrai qualquer sequência solta de dígitos do
  texto, porque isso gerava falso positivo por coincidência numérica com números de emenda parlamentar (testado e corrigido em
  2026-09-17). Não testa contra o Nº de Emenda do SICONV especificamente (testado e descartado em 2026-09-17: "emendas de
  bancada" financiam 5-6 propostas SICONV ao mesmo tempo, então bater só pelo número da emenda associava a transferência a uma
  proposta arbitrária/errada). <strong>5 — Soma Substring (Nº Original):</strong> caso de Termos de Adesão do Fundo a Fundo lançados no SIGGO como
  2 registros fracionados por "Nível de Desempenho" (NUORIGINAL termina em "-ND3"/"-ND4" etc.) — soma o valor dos registros do
  mesmo concedente com o mesmo NUORIGINAL sem esse sufixo e compara com o valor total do plano de ação na fonte (incluído em
  2026-09-17 a partir de casos reais como "TA 21/2025-MQV-ND3"+"TA 21/2025-MQV-ND4"). <strong>6 — Beneficiário+Data+Valor:</strong> nome oficial da UG
  (via CNPJ, mesma fonte <code>UNIDADEGESTORA</code> dos dois lados) + data exata de vigência + valor — só Fundo a Fundo tem
  data bem preenchida. <strong>7 — Beneficiário+Ano+Valor:</strong> mesmo nome + valor, só o ano precisa bater (Gestão de
  Parcerias, Fundo a Fundo e Transferências Especiais). <strong>8 — Beneficiário+Valor:</strong> nome + valor, sem exigir data
  — em Transferências Especiais tenta também o nome do Órgão Executor (ex: "Secretaria de Estado de Saúde"), já que o
  beneficiário formal da API é sempre o ente "DISTRITO FEDERAL", enquanto o executor costuma ser quem aparece de fato em
  <code>COBENEFICIADO</code> no SIGGO. <strong>Nas camadas 6, 7, 8 e 9</strong>, o nome do beneficiário é tentado tanto por
  <code>COBENEFICIADO</code> quanto pela <strong>UG registrante</strong> (<code>COUG</code> + <code>UNIDADEGESTORA.NOUG</code>) — em
  saúde/educação é comum o usuário do SIGGO cadastrar a Secretaria como <code>COBENEFICIADO</code>, mas quem de fato aparece como
  recebedor na API é o Fundo (ex: Fundo de Saúde do DF), que corresponde ao <code>COUG</code> do lançamento (incluído em
  2026-09-17 a partir de um caso real: NUTRANSFERENCIA 30849, COBENEFICIADO="Secretaria de Saúde"/COUG="Fundo de Saúde do DF",
  deveria bater e não batia com a parceria 202500042786, cujo recebedor na API é "FUNDO DE SAÚDE DO DISTRITO FEDERAL").
  <strong>9 — Beneficiário+Objeto:</strong> nome + trecho de texto (não numérico) em comum entre os dois objetos, 15+
  caracteres. A camada "Valor" (sem beneficiário, só valor bater) foi removida em 2026-09-17 por ser uma chave fria demais
  (sem nome nem data) e sujeita a falso positivo em valores redondos/repetidos (ver memória <code>project_lacuna_saude_fundoafundo</code>) —
  registros que só bateriam por essa camada aparecem como "-" em <strong>Encontrado em</strong>. Também corrigido em 2026-09-17:
  quando <code>COBENEFICIADO</code> vem em CNPJ puro (não no formato "UG-sequencial"), o nome da UG agora é buscado também por
  CNPJ, não só por código de UG — antes exibia o CNPJ cru na coluna Beneficiário nesses casos.<br><br>
  <strong>Aba Resumo:</strong> consolida a aba Cruzamento sob duas óticas. <strong>Do SIGGO para o TransfereGov</strong> —
  quantos registros do SIGGO foram localizados, por camada e por fonte, e quais UGs beneficiárias têm mais registros sem
  correspondência (candidatas a regularização). <strong>Do TransfereGov para o SIGGO</strong> — tabela "Cobertura por fonte"
  (quantos convênios/parcerias/planos de ação de cada fonte já foram localizados no SIGGO) e tabela "Pendentes no
  TransfereGov", que lista os identificadores de cada fonte com valor pago/repassado já executado (> 0) mas que nenhum
  registro do SIGGO cita — instrumentos ainda não celebrados ou com valor zerado são propositalmente excluídos dessa
  lista, por estarem em fase inicial e não representarem pendência real.
</div>

<script>
const DADOS = window.DADOS;
const COLUNAS = window.COLUNAS;
const COLUNAS_NUM = window.COLUNAS_NUM;
const ORDEM = window.ORDEM;
const LINHAS_ATUAIS = window.LINHAS_ATUAIS;

const SUBTABELAS_RESUMO = ['resumo_ug', 'resumo_pendentes'];

function mostrarAba(id) {{
  document.querySelectorAll('.aba-conteudo').forEach(el => el.classList.remove('ativo'));
  document.querySelectorAll('.aba-btn').forEach(el => el.classList.remove('ativo'));
  document.getElementById('conteudo-' + id).classList.add('ativo');
  document.querySelector(`.aba-btn[data-aba="${{id}}"]`).classList.add('ativo');
  if (id === 'resumo') {{
    SUBTABELAS_RESUMO.forEach(sub => {{ if (!ORDEM[sub]) renderizar(sub, DADOS[sub]); }});
    return;
  }}
  if (!ORDEM[id]) renderizar(id, DADOS[id]);
}}

function renderizar(id, linhas) {{
  LINHAS_ATUAIS[id] = linhas;
  const tbody = document.querySelector(`#tabela-${{id}} tbody`);
  const cols = COLUNAS[id];
  const numCols = COLUNAS_NUM[id] || [];
  tbody.innerHTML = linhas.map(row =>
    '<tr>' + cols.map((c, i) => `<td class="${{numCols[i] ? 'num' : ''}}" title="${{String(row[c]).replace(/"/g,'&quot;')}}">${{row[c]}}</td>`).join('') + '</tr>'
  ).join('');
  // Nem toda tabela tem barra de filtro/contador (ex: as sub-tabelas da aba
  // Resumo) - so atualiza se o elemento existir.
  const contador = document.getElementById('contador-' + id);
  if (contador) contador.textContent = linhas.length + ' registros';
}}

function aplicarFiltros(id) {{
  const ente = (document.getElementById('fe-' + id).value || '').toLowerCase();
  const ano = document.getElementById('fa-' + id).value;
  const sit = document.getElementById('fs-' + id).value;
  const elMod = document.getElementById('fm-' + id);
  const mod = elMod ? elMod.value : '';
  const livre = (document.getElementById('fl-' + id).value || '').toLowerCase();

  const linhas = DADOS[id].filter(row => {{
    if (ente && !String(row['Beneficiário'] || '').toLowerCase().includes(ente)) return false;
    if (ano && String(row['Ano']) !== ano) return false;
    if (sit && row['Situação'] !== sit) return false;
    if (mod && row['Modalidade'] !== mod) return false;
    if (livre && !Object.values(row).some(v => String(v).toLowerCase().includes(livre))) return false;
    return true;
  }});
  renderizar(id, linhas);
}}

function limparFiltros(id) {{
  document.getElementById('fe-' + id).value = '';
  document.getElementById('fa-' + id).value = '';
  document.getElementById('fs-' + id).value = '';
  const elMod = document.getElementById('fm-' + id);
  if (elMod) elMod.value = '';
  document.getElementById('fl-' + id).value = '';
  renderizar(id, DADOS[id]);
}}

function aplicarFiltrosSiggo() {{
  const id = 'siggo';
  const esp = document.getElementById('fesp-' + id).value;
  const nt = (document.getElementById('fnt-' + id).value || '').toLowerCase();
  const con = (document.getElementById('fcon-' + id).value || '').toLowerCase();
  const ben = (document.getElementById('fben-' + id).value || '').toLowerCase();
  const sia = (document.getElementById('fsia-' + id).value || '').toLowerCase();
  const livre = (document.getElementById('fl-' + id).value || '').toLowerCase();

  const linhas = DADOS[id].filter(row => {{
    if (esp && row['Espécie'] !== esp) return false;
    if (nt && !String(row['Nº Transferência'] || '').toLowerCase().includes(nt)) return false;
    if (con && !String(row['Concedente (CNPJ)'] || '').toLowerCase().includes(con)) return false;
    if (ben && !String(row['Beneficiário'] || '').toLowerCase().includes(ben)) return false;
    if (sia && !String(row['Nº SICONV/SIAFI'] || '').toLowerCase().includes(sia)) return false;
    if (livre && !Object.values(row).some(v => String(v).toLowerCase().includes(livre))) return false;
    return true;
  }});
  renderizar(id, linhas);
}}

function limparFiltrosSiggo() {{
  const id = 'siggo';
  document.getElementById('fesp-' + id).value = '';
  document.getElementById('fnt-' + id).value = '';
  document.getElementById('fcon-' + id).value = '';
  document.getElementById('fben-' + id).value = '';
  document.getElementById('fsia-' + id).value = '';
  document.getElementById('fl-' + id).value = '';
  renderizar(id, DADOS[id]);
}}

function aplicarFiltrosCruzamento() {{
  const id = 'cruzamento';
  const fonte = document.getElementById('ffonte-' + id).value;
  const esp = document.getElementById('fesp-' + id).value;
  const ben = (document.getElementById('fben-' + id).value || '').toLowerCase();
  const livre = (document.getElementById('fl-' + id).value || '').toLowerCase();

  const linhas = DADOS[id].filter(row => {{
    if (fonte && row['Encontrado em'] !== fonte) return false;
    if (esp && row['Espécie'] !== esp) return false;
    if (ben && !String(row['Beneficiário'] || '').toLowerCase().includes(ben)) return false;
    if (livre && !Object.values(row).some(v => String(v).toLowerCase().includes(livre))) return false;
    return true;
  }});
  renderizar(id, linhas);
}}

function limparFiltrosCruzamento() {{
  const id = 'cruzamento';
  document.getElementById('ffonte-' + id).value = '';
  document.getElementById('fesp-' + id).value = '';
  document.getElementById('fben-' + id).value = '';
  document.getElementById('fl-' + id).value = '';
  renderizar(id, DADOS[id]);
}}

function valorOrdenavel(v) {{
  const s = String(v);
  const dm = s.match(/^(\\d{{2}})\\/(\\d{{2}})\\/(\\d{{4}})$/);
  if (dm) return Number(dm[3] + dm[2] + dm[1]);
  const n = parseFloat(s.replace(/\\./g,'').replace(',','.'));
  return isNaN(n) ? v : n;
}}

function compararOrdenavel(a, b, chave, asc) {{
  let va = valorOrdenavel(a[chave]), vb = valorOrdenavel(b[chave]);
  // Colunas como "Confiança" misturam valores numéricos (tiers "1 - ...",
  // "2 - ...") com o texto "-" (sem correspondência). Comparar number < string
  // no JS converte a string para NaN, e qualquer comparação com NaN retorna
  // false nos dois sentidos - o sort trata como "empate" e a ordenação trava/
  // embaralha. Corrigido em 2026-09-17: quando os tipos divergem, volta a
  // comparar como texto (string) para os dois lados, de forma consistente.
  if (typeof va !== typeof vb) {{ va = String(a[chave]); vb = String(b[chave]); }}
  if (va < vb) return asc ? -1 : 1;
  if (va > vb) return asc ? 1 : -1;
  return 0;
}}

function ordenar(id, colIdx) {{
  const cols = COLUNAS[id];
  const chave = cols[colIdx];
  ORDEM[id] = ORDEM[id] === chave ? null : chave;
  const asc = ORDEM[id] === chave;
  document.querySelectorAll(`#tabela-${{id}} .si`).forEach(el => el.textContent = '⇅');
  const setaAtual = document.getElementById(`si-${{id}}-${{colIdx}}`);
  if (setaAtual) setaAtual.textContent = asc ? '↑' : '↓';
  const base = LINHAS_ATUAIS[id] || DADOS[id];
  const linhas = [...base].sort((a, b) => compararOrdenavel(a, b, chave, asc));
  renderizar(id, linhas);
}}

document.addEventListener('DOMContentLoaded', () => {{
  // Pre-ordena e renderiza a aba Cruzamento pela Data de celebração mais
  // recente primeiro, mesmo sem ser a aba inicial visível - assim já fica
  // pronta quando o usuário clicar nela.
  const idCruz = 'cruzamento';
  const chaveData = 'Data Celeb.';
  const idxData = (COLUNAS[idCruz] || []).indexOf(chaveData);
  if (idxData >= 0) {{
    ORDEM[idCruz] = chaveData;
    const linhas = [...DADOS[idCruz]].sort((a, b) => compararOrdenavel(a, b, chaveData, false));
    const seta = document.getElementById(`si-${{idCruz}}-${{idxData}}`);
    if (seta) seta.textContent = '↓';
    renderizar(idCruz, linhas);
  }}
  // Abre direto na aba inicial.
  mostrarAba('{aba_inicial}');
}});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
