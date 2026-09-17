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


CNPJS_GDF = carregar_cnpjs_gdf()


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
    df = df[eh_gdf(df["cnpj_ente_recebedor"])].copy()

    df["situacao"] = df["in_situacao_parceria"].fillna(df["situacao_proposta"])
    df["valor_global"] = num(df.get("nr_vlr_total")).where(
        num(df.get("nr_vlr_total")) > 0, num(df.get("vl_total_planejamento_gastos"))
    )
    df["valor_repassado"] = df["id_parceria"].map(repasse_por_parceria).fillna(0)

    saida = pd.DataFrame({
        "Nº Parceria": df["cd_parceria"].fillna(df["id_proposta"].astype("Int64").astype(str)),
        "Ente beneficiário": df["nm_ente_recebedor"],
        "Objeto": df["ds_objeto"].astype(str).str.slice(0, 100),
        "Situação": df["situacao"],
        "Ano": df["ano_proposta"],
        "Valor global (R$)": df["valor_global"],
        "Valor repassado (R$)": df["valor_repassado"],
    })
    params = {
        "Nº Parceria": "parceria.cd_parceria",
        "Ente beneficiário": "proposta.nm_ente_recebedor",
        "Objeto": "proposta.ds_objeto",
        "Situação": "parceria.in_situacao_parceria",
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

    colunas = ["Nº Plano de Ação", "Nº Emenda Parlamentar", "Ente beneficiário", "Órgão executor",
               "Parlamentar autor da emenda", "Situação", "Início execução", "Fim execução",
               "Ano", "Valor global (R$)", "Valor pago (R$)"]
    params = {
        "Nº Plano de Ação": "plano_acao.codigo_plano_acao",
        "Nº Emenda Parlamentar": "plano_acao.numero_emenda_parlamentar_plano_acao",
        "Ente beneficiário": "beneficiario.nome_beneficiario",
        "Órgão executor": "executor.nome_executor",
        "Parlamentar autor da emenda": "plano_acao.nome_parlamentar_emenda_plano_acao",
        "Situação": "plano_acao.situacao_plano_acao",
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
    plano = plano[eh_gdf(plano["cnpj_beneficiario"])].copy()
    plano["valor"] = num(plano.get("valor_custeio_plano_acao")) + num(plano.get("valor_investimento_plano_acao"))
    plano["valor_pago"] = plano["id_plano_acao"].map(valor_pago_por_plano).fillna(0)
    plano["orgao_executor"] = plano["id_plano_acao"].map(executor_por_plano).fillna("")
    plano["inicio_execucao"] = plano["id_plano_acao"].map(inicio_por_plano)
    plano["fim_execucao"] = plano["id_plano_acao"].map(fim_por_plano)

    saida = pd.DataFrame({
        "Nº Plano de Ação": plano["codigo_plano_acao"],
        "Nº Emenda Parlamentar": plano["numero_emenda_parlamentar_plano_acao"],
        "Ente beneficiário": plano["nome_beneficiario"],
        "Órgão executor": plano["orgao_executor"],
        "Parlamentar autor da emenda": plano["nome_parlamentar_emenda_plano_acao"],
        "Situação": plano["situacao_plano_acao"],
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

    colunas = ["Nº Plano de Ação", "Ente concedente", "Ente beneficiário", "Fundo vinculado",
               "Situação", "Início vigência", "Fim vigência", "Ano",
               "Valor global (R$)", "Valor repassado (R$)", "Valor pago (R$)"]
    params = {
        "Nº Plano de Ação": "plano_acao.codigo_plano_acao",
        "Ente concedente": "plano_acao.nome_orgao_repassador_plano_acao",
        "Ente beneficiário": "plano_acao.nome_ente_recebedor_plano_acao",
        "Fundo vinculado": "plano_acao.nome_fundo_vinculado_plano_acao",
        "Situação": "plano_acao.situacao_plano_acao",
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
        "Ente concedente": plano["nome_orgao_repassador_plano_acao"],
        "Ente beneficiário": plano["nome_ente_recebedor_plano_acao"],
        "Fundo vinculado": plano["nome_fundo_vinculado_plano_acao"],
        "Situação": plano["situacao_plano_acao"],
        "Início vigência": fmt_data(plano["data_inicio_vigencia_plano_acao"]),
        "Fim vigência": fmt_data(plano["data_fim_vigencia_plano_acao"]),
        "Ano": extrair_ano(plano["data_inicio_vigencia_plano_acao"]),
        "Valor global (R$)": num(plano.get("valor_total_plano_acao")),
        "Valor repassado (R$)": num(plano.get("valor_total_repasse_plano_acao")),
        "Valor pago (R$)": plano["valor_pago"],
    })
    return saida.sort_values("Ano", ascending=False, na_position="last"), params


# ---------------------------------------------------------------------------
# 4) SICONV legado
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
        "Ente concedente": df["DESC_ORGAO_SUP"],
        "Ente beneficiário": df["NM_PROPONENTE"],
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
        "Ente concedente": "proposta.DESC_ORGAO_SUP",
        "Ente beneficiário": "proposta.NM_PROPONENTE",
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
    t = t[(~concedente_e_gdf) & beneficiario_e_gdf].copy()

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
    colunas = ["Nº Transferência", "Espécie", "Beneficiário", "Objeto", "NUTRANSFSIAFI", "NUORIGINAL",
               "Valor transferência (R$)", "Data de celebração", "Encontrado em", "Confiança", "Identificador na fonte"]
    params = {
        "Nº Transferência": "TRANSFERENCIA.NUTRANSFERENCIA",
        "Espécie": "TRANSFERENCIA.INESPECIE",
        "Beneficiário": "TRANSFERENCIA.COBENEFICIADO + UNIDADEGESTORA.NOUG",
        "Objeto": "TRANSFERENCIA.TXOBJETORESUMIDO",
        "NUTRANSFSIAFI": "TRANSFERENCIA.NUTRANSFSIAFI",
        "NUORIGINAL": "TRANSFERENCIA.NUORIGINAL",
        "Valor transferência (R$)": "TRANSFERENCIA.VATRANSFERENCIA",
        "Data de celebração": "TRANSFERENCIA.DACELEBRACAO",
        "Encontrado em": "resultado do cruzamento (ver metodologia no rodapé)",
        "Confiança": "ver hierarquia de 9 camadas no rodapé (Nº Convênio > substrings > benef.+data/ano/valor > benef.+objeto > valor)",
        "Identificador na fonte": "NR_CONVENIO / cd_parceria / codigo_plano_acao, conforme a fonte",
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

    concedente_e_gdf = eh_gdf_qualquer_formato(t["COCONCENTE"], ugs_df)
    beneficiario_e_gdf = eh_gdf_qualquer_formato(t["COBENEFICIADO"], ugs_df)
    t = t[(~concedente_e_gdf) & beneficiario_e_gdf].copy()

    t["especie"] = t["INESPECIE"].map(
        lambda v: f"{int(v):02d} - {ESPECIE_LABELS.get(int(v), 'Código ' + str(int(v)))}" if pd.notna(v) else ""
    )
    prefixo_benef = t["COBENEFICIADO"].astype(str).str.strip().str.split("-").str[0]
    nome_benef = prefixo_benef.map(ug_nome)
    t["beneficiario_fmt"] = t["COBENEFICIADO"].astype(str).str.strip()
    tem_nome = nome_benef.notna()
    t.loc[tem_nome, "beneficiario_fmt"] = nome_benef[tem_nome] + " (" + t.loc[tem_nome, "COBENEFICIADO"].astype(str).str.strip() + ")"
    t["nome_canonico_siggo"] = nome_benef  # nome oficial da UG, sem sufixo - usado nas camadas de benef.
    t["nutransfsiafi_limpo"] = pd.to_numeric(t["NUTRANSFSIAFI"], errors="coerce").fillna(0).astype("int64").astype(str)
    t["nuoriginal_digitos"] = _so_digitos(t["NUORIGINAL"])
    t["ano_celebracao"] = extrair_ano(t["DACELEBRACAO"])
    t["ano_vigencia"] = extrair_ano(t["DAINIVIGENCIA"])

    # --- Fonte 1: SICONV (chave exata) ---
    convenio = carregar("df_siconv_convenio.csv", dtype={"NR_CONVENIO": str})
    proposta_sic = carregar("df_siconv_proposta.csv")
    sic = convenio.merge(proposta_sic[["ID_PROPOSTA", "IDENTIF_PROPONENTE"]], on="ID_PROPOSTA", how="left")
    sic = sic[eh_gdf(sic["IDENTIF_PROPONENTE"])]
    nrs_siconv = set(sic["NR_CONVENIO"].dropna().astype(str).str.strip())

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
        conta_faf = pd.DataFrame(columns=["conta_digitos", "codigo_plano_acao"])

    # --- Fonte 4: Transferencias Especiais ---
    esp_plano = carregar("df_especiais_plano_acao.csv")
    esp_benef = carregar("df_especiais_beneficiario.csv")
    esp = esp_plano.merge(esp_benef[["id_beneficiario", "cnpj_beneficiario"]], on="id_beneficiario", how="left")
    esp = esp[eh_gdf(esp["cnpj_beneficiario"])].copy()
    esp["valor_total"] = (num(esp["valor_custeio_plano_acao"]) + num(esp["valor_investimento_plano_acao"])).round(2)
    esp["nome_canonico"] = _nome_canonico(esp["cnpj_beneficiario"], cnpj_para_nome)
    esp["codigo_digitos"] = _so_digitos(esp["codigo_plano_acao"])
    esp["objeto_upper"] = esp["nome_objeto"].astype(str).str.upper().str.strip()
    esp["ano_plano_acao"] = pd.to_numeric(esp.get("ano_plano_acao"), errors="coerce")
    valor_para_esp = esp[esp["valor_total"] > 0].groupby("valor_total")["codigo_plano_acao"].apply(list)
    benef_valor_para_esp = esp[(esp["valor_total"] > 0) & esp["nome_canonico"].notna()].groupby(
        ["nome_canonico", "valor_total"]
    )["codigo_plano_acao"].apply(list)
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
    FONTES_SUBSTRING = ((digitos_siconv, "SICONV Legado"),
                         (digitos_parceria, "Gestão de Parcerias"),
                         (digitos_faf, "Fundo a Fundo"),
                         (digitos_esp, "Transferências Especiais"))

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
    if not conta_gp.empty:
        for _, r in conta_gp.iterrows():
            if r["conta_digitos"] and pd.notna(r.get("cd_parceria_str")):
                contas_index.append((r["conta_digitos"], "Gestão de Parcerias", r["cd_parceria_str"]))
    if not conta_faf.empty:
        for _, r in conta_faf.iterrows():
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
        r"(?:SICONV|CONV[EÊ]NIO|CONV\.|CONTRATO\s*DE\s*REPASSE|PARCERIA|PLANO\s*DE\s*A[CÇ][AÃ]O)"
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
    t["objeto_upper"] = t["TXOBJETORESUMIDO"].astype(str).str.upper().str.strip()
    t["objeto_ids"] = t["TXOBJETORESUMIDO"].apply(extrair_ids_objeto)

    def cruzar(row):
        # 1) Nº Convênio (chave exata)
        if row["nutransfsiafi_limpo"] != "0" and row["nutransfsiafi_limpo"] in nrs_siconv:
            return pd.Series(["SICONV Legado", "1 - Nº Convênio", row["nutransfsiafi_limpo"]])

        # 2) Substring (NUORIGINAL x identificador de qualquer fonte, incl. SICONV)
        achado = busca_substring(row["nuoriginal_digitos"])
        if achado:
            fonte, cod = achado
            return pd.Series([fonte, "2 - Substring (Nº Original)", cod])

        # 3) Substring de conta bancária (NUCONTA)
        achado_conta = busca_substring_conta(row["nuconta_digitos"])
        if achado_conta:
            fonte, cod = achado_conta
            return pd.Series([fonte, "3 - Substring (Nº Conta)", cod])

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

        # 5) Beneficiário + Data exata + Valor (só Fundo a Fundo tem data confiável)
        if v > 0 and pd.notna(nome) and data_cel:
            chave = (nome, data_cel, v)
            if chave in benef_data_exata_valor_para_faf.index:
                ids = benef_data_exata_valor_para_faf.loc[chave]
                return pd.Series(["Fundo a Fundo", "5 - Beneficiário+Data+Valor", " | ".join(map(str, ids))])

        # 6) Beneficiário + Ano + Valor
        if v > 0 and pd.notna(nome):
            for ano in {ano_cel, ano_vig} - {None}:
                for tabela, fonte in ((benef_ano_valor_para_parceria, "Gestão de Parcerias"),
                                      (benef_ano_valor_para_faf, "Fundo a Fundo"),
                                      (benef_ano_valor_para_esp, "Transferências Especiais")):
                    chave = (nome, ano, v)
                    if chave in tabela.index:
                        ids = tabela.loc[chave]
                        return pd.Series([fonte, "6 - Beneficiário+Ano+Valor", " | ".join(map(str, ids))])

        # 7) Beneficiário + Valor
        if v > 0 and pd.notna(nome):
            for tabela, fonte in ((benef_valor_para_parceria, "Gestão de Parcerias"),
                                  (benef_valor_para_faf, "Fundo a Fundo"),
                                  (benef_valor_para_esp, "Transferências Especiais")):
                chave = (nome, v)
                if chave in tabela.index:
                    ids = tabela.loc[chave]
                    return pd.Series([fonte, "7 - Beneficiário+Valor", " | ".join(map(str, ids))])

        # 8) Beneficiário + Objeto (substring de texto, nao de numero)
        achado_obj = busca_objeto(row["objeto_upper"], nome)
        if achado_obj:
            fonte, cod = achado_obj
            return pd.Series([fonte, "8 - Beneficiário+Objeto", cod])

        # 9) Valor apenas (sem beneficiário - ultimo recurso)
        if v > 0:
            for tabela, fonte in ((valor_para_parceria, "Gestão de Parcerias"),
                                  (valor_para_faf, "Fundo a Fundo"),
                                  (valor_para_esp, "Transferências Especiais")):
                if v in tabela.index:
                    ids = tabela.loc[v]
                    return pd.Series([fonte, "9 - Valor", " | ".join(map(str, ids))])

        return pd.Series(["Sem correspondência", "", ""])

    t["DACELEBRACAO_fmt"] = fmt_data(t["DACELEBRACAO"])
    t[["encontrado_em", "confianca", "identificador"]] = t.apply(cruzar, axis=1)

    nutransfsiafi_exibicao = pd.to_numeric(t["NUTRANSFSIAFI"], errors="coerce")
    saida = pd.DataFrame({
        "Nº Transferência": t["NUTRANSFERENCIA"],
        "Espécie": t["especie"],
        "Beneficiário": t["beneficiario_fmt"],
        "Objeto": t["TXOBJETORESUMIDO"].astype(str).str.slice(0, 100),
        "NUTRANSFSIAFI": nutransfsiafi_exibicao.where(nutransfsiafi_exibicao > 0, "").astype(str).replace("0.0", ""),
        "NUORIGINAL": t["NUORIGINAL"],
        "Valor transferência (R$)": num(t["VATRANSFERENCIA"]),
        "Data de celebração": fmt_data(t["DACELEBRACAO"]),
        "Encontrado em": t["encontrado_em"],
        "Confiança": t["confianca"],
        "Identificador na fonte": t["identificador"],
    })
    return saida.sort_values("Nº Transferência", ascending=False), params


# ---------------------------------------------------------------------------
# Montagem do HTML
# ---------------------------------------------------------------------------

ABAS = [
    {"id": "parcerias", "titulo": "Gestão de Parcerias", "icone": "🤝",
     "fonte": "API /parcerias — endpoints /proposta + /parceria + /documento-habil + /ordem-pagamento",
     "coluna_ente": "Ente beneficiário", "coluna_valor_kpi": "Valor repassado (R$)"},
    {"id": "especiais", "titulo": "Transferências Especiais", "icone": "🏛️",
     "fonte": "API /especiais — endpoints /beneficiarios-especiais + /planos-acao-especiais + /programas-especiais + cadeia empenho→documento hábil→ordem de pagamento",
     "coluna_ente": "Ente beneficiário", "coluna_valor_kpi": "Valor pago (R$)"},
    {"id": "fundoafundo", "titulo": "Fundo a Fundo", "icone": "💰",
     "fonte": "API /fundoafundo — endpoint /planos-acao + cadeia dados bancários→lançamentos→subtransações",
     "coluna_ente": "Ente beneficiário", "coluna_valor_kpi": "Valor pago (R$)"},
    {"id": "siconv", "titulo": "SICONV Legado", "icone": "📄",
     "fonte": "Download CSV — siconv_convenio.csv + siconv_proposta.csv + siconv_emenda.csv",
     "coluna_ente": "Ente beneficiário", "coluna_valor_kpi": "Valor pago (R$)"},
    {"id": "siggo", "titulo": "SIGGO", "icone": "🗄️",
     "fonte": "Oracle SIGGO — MIL2026.TRANSFERENCIA + MIL2026.UNIDADEGESTORA (ainda não cruzado com o TransfereGov)",
     "coluna_ente": "Beneficiário", "coluna_valor_kpi": "Valor transferência (R$)"},
    {"id": "cruzamento", "titulo": "Cruzamento SIGGO×TransfereGov", "icone": "🔗",
     "fonte": "SIGGO (MIL2026.TRANSFERENCIA) cruzado com as 4 fontes do TransfereGov — nº exato (SICONV) ou valor (demais, heurística)",
     "coluna_ente": "Beneficiário", "coluna_valor_kpi": "Valor transferência (R$)"},
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
        <label>Ente beneficiário</label>
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
        <label>Encontrado em</label>
        <select id="ffonte-{aba_id}" onchange="aplicarFiltrosCruzamento()"><option value="">Todas</option>{opts_fonte}</select>
      </div>
      <div class="fg">
        <label>Espécie</label>
        <select id="fesp-{aba_id}" onchange="aplicarFiltrosCruzamento()"><option value="">Todas</option>{opts_especie}</select>
      </div>
      <div class="fg">
        <label>Beneficiário</label>
        <input type="text" id="fben-{aba_id}" placeholder="Nome ou código da UG…" oninput="aplicarFiltrosCruzamento()">
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
        return f'<th class="{classe}" onclick="ordenar(\'{aba_id}\',{i})">{c}{sub}</th>'

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


def main():
    resultados = {
        "parcerias": montar_parcerias(),
        "especiais": montar_especiais(),
        "fundoafundo": montar_fundoafundo(),
        "siconv": montar_siconv(),
        "siggo": montar_siggo(),
        "cruzamento": montar_cruzamento(),
    }
    dados = {aid: df for aid, (df, _) in resultados.items()}
    parametros = {aid: params for aid, (_, params) in resultados.items()}

    abas_html = []
    conteudo_html = []
    for i, aba in enumerate(ABAS):
        aid = aba["id"]
        df = dados[aid]
        ativo = "ativo" if i == 0 else ""
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

        conteudo_html.append(f"""
        <section class="aba-conteudo {'ativo' if i == 0 else ''}" id="conteudo-{aid}">
          <p class="fonte-info">Fonte: {aba['fonte']}</p>
          {filtros_html}
          {tabela_html}
        </section>
        """)

    html = TEMPLATE.format(
        abas_nav="".join(abas_html),
        abas_conteudo="".join(conteudo_html),
        data_geracao=pd.Timestamp.now().strftime("%d/%m/%Y %H:%M"),
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
  <strong>Aba SIGGO:</strong> de 18.624 registros de <code>MIL2026.TRANSFERENCIA</code>, mostra só os <strong>1.928</strong> em que
  o concedente NÃO é o GDF e o beneficiário É o GDF — ou seja, recurso externo (essencialmente da União) recebido pelo GDF, excluindo
  os 16.512 casos em que o próprio GDF é concedente (a órgãos privados/OSCs ou a si mesmo) e os 88 casos intra-GDF. O papel é
  reconhecido tanto em CNPJ quanto no formato "UG-sequencial" usado por <code>COCONCENTE</code>/<code>COBENEFICIADO</code>. Filtro por
  papel, não por espécie — testado e mais preciso (6 espécies como Auxílio/Subvenção/PDAF nunca aparecem como "União→GDF" na base
  inteira, mas Termo de Fomento tem alguns casos raros e legítimos que uma lista de espécies excluiria por engano). Esta aba ainda não
  foi cruzada com as demais (TransfereGov); serve para validação e exploração antes do cruzamento.<br><br>
  <strong>Aba Cruzamento SIGGO×TransfereGov:</strong> para cada um dos registros do SIGGO, tenta achar o instrumento
  correspondente no TransfereGov, sempre pela chave <strong>mais robusta disponível primeiro</strong>, em 9 camadas de confiança
  decrescente: <strong>1 — Nº Convênio:</strong> <code>NUTRANSFSIAFI</code> = <code>NR_CONVENIO</code> no SICONV.
  <strong>2 — Substring (Nº Original):</strong> os dígitos de <code>NUORIGINAL</code> contêm (ou estão contidos em) o
  identificador de qualquer fonte (<code>NR_CONVENIO</code>/<code>cd_parceria</code>/<code>codigo_plano_acao</code>), exigindo
  6+ dígitos em comum. <strong>3 — Substring (Nº Conta):</strong> os dígitos de <code>NUCONTA</code> contêm/estão contidos na
  conta bancária cadastrada na fonte — exige 4+ dígitos em comum. <strong>4 — Substring (Objeto):</strong> procura, no texto do
  <code>TXOBJETORESUMIDO</code>, um número citado logo após palavras-chave como "SICONV", "Convênio" ou "Parceria" (ex: "SICONV
  Nº 825427/2015") e testa esse número contra os identificadores das fontes — não extrai qualquer sequência solta de dígitos do
  texto, porque isso gerava falso positivo por coincidência numérica com números de emenda parlamentar (testado e corrigido em
  2026-09-17). <strong>5 — Beneficiário+Data+Valor:</strong> nome oficial da UG
  (via CNPJ, mesma fonte <code>UNIDADEGESTORA</code> dos dois lados) + data exata de vigência + valor — só Fundo a Fundo tem
  data bem preenchida. <strong>6 — Beneficiário+Ano+Valor:</strong> mesmo nome + valor, só o ano precisa bater (Gestão de
  Parcerias, Fundo a Fundo e Transferências Especiais). <strong>7 — Beneficiário+Valor:</strong> nome + valor, sem exigir data.
  <strong>8 — Beneficiário+Objeto:</strong> nome + trecho de texto (não numérico) em comum entre os dois objetos, 15+
  caracteres. <strong>9 — Valor:</strong> só o valor bate, sem nenhuma outra confirmação — a mais sujeita a falso positivo em
  valores redondos/repetidos (ver memória <code>project_lacuna_saude_fundoafundo</code>).
</div>

<script>
const DADOS = window.DADOS;
const COLUNAS = window.COLUNAS;
const COLUNAS_NUM = window.COLUNAS_NUM;
const ORDEM = window.ORDEM;
const LINHAS_ATUAIS = window.LINHAS_ATUAIS;

function mostrarAba(id) {{
  document.querySelectorAll('.aba-conteudo').forEach(el => el.classList.remove('ativo'));
  document.querySelectorAll('.aba-btn').forEach(el => el.classList.remove('ativo'));
  document.getElementById('conteudo-' + id).classList.add('ativo');
  document.querySelector(`.aba-btn[data-aba="${{id}}"]`).classList.add('ativo');
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
  document.getElementById('contador-' + id).textContent = linhas.length + ' registros';
}}

function aplicarFiltros(id) {{
  const ente = (document.getElementById('fe-' + id).value || '').toLowerCase();
  const ano = document.getElementById('fa-' + id).value;
  const sit = document.getElementById('fs-' + id).value;
  const elMod = document.getElementById('fm-' + id);
  const mod = elMod ? elMod.value : '';
  const livre = (document.getElementById('fl-' + id).value || '').toLowerCase();

  const linhas = DADOS[id].filter(row => {{
    if (ente && !String(row['Ente beneficiário'] || '').toLowerCase().includes(ente)) return false;
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

function ordenar(id, colIdx) {{
  const cols = COLUNAS[id];
  const chave = cols[colIdx];
  ORDEM[id] = ORDEM[id] === chave ? null : chave;
  const asc = ORDEM[id] === chave;
  const base = LINHAS_ATUAIS[id] || DADOS[id];
  const linhas = [...base].sort((a, b) => {{
    let va = a[chave], vb = b[chave];
    const na = parseFloat(String(va).replace(/\\./g,'').replace(',','.'));
    const nb = parseFloat(String(vb).replace(/\\./g,'').replace(',','.'));
    if (!isNaN(na) && !isNaN(nb)) {{ va = na; vb = nb; }}
    if (va < vb) return asc ? -1 : 1;
    if (va > vb) return asc ? 1 : -1;
    return 0;
  }});
  renderizar(id, linhas);
}}

document.addEventListener('DOMContentLoaded', () => {{
  const primeira = document.querySelector('.aba-btn').dataset.aba;
  renderizar(primeira, DADOS[primeira]);
}});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
