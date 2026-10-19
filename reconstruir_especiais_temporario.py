"""
Reconstrucao TEMPORARIA dos dados de Transferencias Especiais (GDF).

Contexto: a partir de 17/10/2026 a API de Especiais devolveu 0 planos de acao e os
CSVs brutos de 16/10 (15 planos) foram sobrescritos. Esta reconstrucao parte da tabela
ja processada do painel publicado em 16/10/2026 (reconstruido_temporario/fonte_painel_16102026.csv)
e gera arquivos no mesmo formato dos CSVs brutos, em reconstruido_temporario/.

Uso pelo painel: gerar_painel_transferegov_df.carregar() so le estes arquivos quando o CSV real
correspondente esta SEM dados. Quando a API voltar e a extracao gravar dados reais, os
reconstruidos deixam de ser usados automaticamente (nao precisam ser apagados).
Campos que nao existem na tabela do painel (conta bancaria, objeto, empenhos reais) ficam vazios.
"""

import re
import unicodedata
from pathlib import Path

import pandas as pd

from extrair_transferegov_df import COLUNAS_ESPECIAIS

PASTA = Path(__file__).resolve().parent
SAIDA = PASTA / "reconstruido_temporario"


def valor(txt):
    return float(str(txt).replace(".", "").replace(",", ".")) if str(txt).strip() else 0.0


def iso(d):
    d = str(d).strip()
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", d)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def norm(x):
    x = unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", x)


def main():
    fonte = pd.read_csv(SAIDA / "fonte_painel_16102026.csv", dtype=str).fillna("")
    benef = pd.read_csv(PASTA / "df_especiais_beneficiario.csv", dtype=str)
    id_benef = benef["id_beneficiario"].iloc[0]
    ugs = pd.read_csv(PASTA / "unidadegestora_bruto.csv", dtype=str).fillna("")
    cnpj_por_nome = {}
    for r in ugs.itertuples():
        for nome in (r.NOUG, r.NORAZAOSOCIAL):
            if norm(nome):
                cnpj_por_nome.setdefault(norm(nome), r.NUCGC.strip())

    concedentes = {c: 900000 + i for i, c in enumerate(sorted(fonte["Concedente"].unique()), start=1)}
    planos, execs, pts, emps, dhs, ops = [], [], [], [], [], []
    for i, r in enumerate(fonte.itertuples(index=False), start=1):
        idp = 900000 + i
        global_ = valor(r[11])
        pago = valor(r[12])
        planos.append({
            "id_plano_acao": idp, "codigo_plano_acao": r[0], "ano_plano_acao": r[10], "situacao_plano_acao": r[6],
            "data_aceite_plano_acao": iso(r[7]), "nome_parlamentar_emenda_plano_acao": r[5],
            "numero_emenda_parlamentar_plano_acao": r[1], "valor_custeio_plano_acao": 0.0,
            "valor_investimento_plano_acao": global_, "id_beneficiario": id_benef, "id_programa": concedentes[r[2]], "nome_objeto": "",
        })
        if r[4]:
            execs.append({"id_executor": idp, "id_plano_acao": idp, "nome_executor": r[4], "cnpj_executor": cnpj_por_nome.get(norm(r[4]), ""),
                          "vl_custeio_executor": 0.0, "vl_investimento_executor": global_})
        if iso(r[8]) or iso(r[9]):
            pts.append({"id_plano_trabalho": idp, "id_plano_acao": idp, "data_inicio_execucao_plano_trabalho": iso(r[8]),
                        "data_fim_execucao_plano_trabalho": iso(r[9])})
        if pago > 0:
            emps.append({"id_empenho": 800000 + i, "id_plano_acao": idp})
            dhs.append({"id_dh": 700000 + i, "id_empenho": 800000 + i, "valor_dh": pago})
            ops.append({"id_op_ob": 600000 + i, "id_dh": 700000 + i, "descricao_situacao_op": "PAGO"})
    progs = [{"id_programa": pid, "nome_orgao_programa": nome} for nome, pid in concedentes.items()]

    mapa = {"df_especiais_plano_acao.csv": planos, "df_especiais_executor.csv": execs, "df_especiais_plano_trabalho.csv": pts,
            "df_especiais_empenho.csv": emps, "df_especiais_documento_habil.csv": dhs, "df_especiais_ordem_pagamento.csv": ops,
            "df_especiais_programa.csv": progs}
    for nome, linhas in mapa.items():
        df = pd.DataFrame(linhas).reindex(columns=COLUNAS_ESPECIAIS[nome])
        df.to_csv(SAIDA / nome, index=False, encoding="utf-8-sig")
        print(f"{nome}: {len(df)} registros")


if __name__ == "__main__":
    main()
