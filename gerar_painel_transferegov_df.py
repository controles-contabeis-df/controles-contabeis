"""
Painel de Transferencias do TransfereGov (transferegov.html) - rotina diaria (atualizar_paineis.ps1, fase B).

Cruza TODOS os instrumentos das quatro fontes do TransfereGov (Gestao de Parcerias, Transferencias Especiais,
Fundo a Fundo e Discricionarias e Legais/SICONV) com o SIGGO SOMENTE pela chave: o identificador do instrumento na
API no Nº TransfereGov (NUTRANSFSIAFI) ou, de forma exata, no Numero Original (inclusive com pontos, como 803.975/2014);
no SICONV, tambem o nº da proposta (registro unico da mesma UG). Sem camadas fracas (conta, objeto, valor, nome).

Abas: Gestao de Parcerias, Transferencias Especiais, Fundo a Fundo, Discricionarias e Legais, SIGGO,
Cruzamento otica SIGGo, Cruzamento otica TransfereGov e Resumo (com a visao por UG). Inconsistencias e providencias
detalhadas ficam no CSV exportavel. As funcoes de montagem de cada fonte estao em painel_transferegov_base.py.
Versao anterior preservada em historico/gerar_painel_transferegov_df_ANTES_v2_20261026.py.
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "extracao_pontual"))
import painel_transferegov_base as g  # noqa: E402  (funcoes de montagem de cada fonte e utilitarios)
import gerar_pendencias_por_ug as gp  # noqa: E402
import gerar_planilhas_ug as gu  # noqa: E402

SAIDA = RAIZ / "transferegov.html"
dig = lambda x: re.sub(r"\D", "", str(x if pd.notna(x) else ""))
BAIXAS = ("ANULAD", "CANCELAD", "RESCINDID", "IMPEDID", "REJEITAD")
ADEQUADO, INCONSISTENTE, NAO_LOC = "Adequado", "Inconsistente", "Não localizado"
BAIXA, ANTERIOR = "Baixa", "Anterior"
SEM_UG = "UG NÃO ENCONTRADA - DISTRITO FEDERAL"
GRAVIDADE = {INCONSISTENTE: 0, NAO_LOC: 1, ADEQUADO: 2, BAIXA: 3, ANTERIOR: 4}
ROTULO_GRUPO = {ADEQUADO: "Adequado", INCONSISTENTE: "Inconsistente", NAO_LOC: "Não localizado",
                BAIXA: "Anulado, cancelado, rescindido, impedido ou rejeitado", ANTERIOR: "Ainda não celebrado (proposta, análise, elaboração)"}
CORES_GRUPO = {ADEQUADO: "#1a7a44", INCONSISTENTE: "#f0a500", NAO_LOC: "#c0392b", BAIXA: "#8a97b0", ANTERIOR: "#c5cddc"}
TIER = {"Nº TransfereGov": "1 - Nº TransfereGov", "Nº Original": "2 - Nº Original (exato)", "Nº da proposta": "3 - Nº da proposta (SICONV)"}


def vazio(x):
    return x is None or (isinstance(x, float) and pd.isna(x)) or str(x).strip() in ("", "nan", "None")


def norm_prop(x):
    m = re.fullmatch(r"\s*0*(\d+)\s*/\s*(\d{4})\s*", str(x))
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def fmt_dt(d):
    return d.strftime("%d/%m/%Y") if d is not None and not pd.isna(d) else ""


def fmt_int(n):
    return f"{int(n):,}".replace(",", ".")


# --------------------------------------------------------------------------- universo e cruzamento
def montar_universo(dfs):
    out = []
    for fonte, info in g.FONTE_TAB_INFO.items():
        df = dfs[info["aba"]][0]
        for _, r in df.iterrows():
            ident = str(r[info["id_col"]]).strip()
            ident = ident[:-2] if ident.endswith(".0") else ident
            if info["aba"] == "especiais":
                benef = r["Órgão executor"] if not vazio(r["Órgão executor"]) else r["Beneficiário"]
            elif info["aba"] == "fundoafundo":
                benef = r.get("Fundo vinculado") if not vazio(r.get("Fundo vinculado")) else r["Beneficiário"]
            else:
                benef = r["Beneficiário"]
            out.append(dict(
                fonte=fonte, ident=ident, concedente=gu.titulo(r["Concedente"]), benef=benef,
                situacao=r["Situação"] if not vazio(r["Situação"]) else "",
                data=r["Data de celebração"] if not vazio(r.get("Data de celebração")) else "",
                valor_global=float(g.num(pd.Series([r["Valor global (R$)"]])).iloc[0]),
                valor_repassado=float(g.num(pd.Series([r[info["valor_col"]]])).iloc[0])))
    return out


def construir(dfs, siggo):
    """Cruza TODOS os instrumentos das APIs com o SIGGo SOMENTE pela chave.
    Retorna (linhas da otica API, mapa registro -> [(identificador, fonte, grupo, tipo da chave)])."""
    universo = montar_universo(dfs)
    conv = g.carregar("df_siconv_convenio.csv", dtype=str).fillna("")
    prop = g.carregar("df_siconv_proposta.csv", dtype=str).fillna("")
    mm = conv.merge(prop, on="ID_PROPOSTA", how="left").drop_duplicates("NR_CONVENIO")
    sic = {g.chave_id(r["NR_CONVENIO"]): r.to_dict() for _, r in mm.iterrows()}
    par = g.carregar("df_parcerias_parceria.csv", dtype=str).fillna("")
    sei = {g.chave_id(r["cd_parceria"]): r["cd_processo_sei"] for _, r in par.iterrows()}
    pa = g.carregar("df_especiais_plano_acao.csv", dtype=str).fillna("")
    emenda_plano = {g.chave_id(r["codigo_plano_acao"]): dig(r["numero_emenda_parlamentar_plano_acao"]) for _, r in pa.iterrows()}
    det = {"Discricionárias e Legais": gp.detalhes_siconv(), "Gestão de Parcerias": gp.detalhes_parcerias(),
           "Transferências Especiais": gp.detalhes_especiais(), "Fundo a Fundo": gp.detalhes_faf()}

    ugs = pd.read_csv(RAIZ / "unidadegestora_bruto.csv", dtype=str).fillna("")
    ugs["cnpj"] = ugs.NUCGC.map(lambda x: dig(x).lstrip("0"))
    por_cnpj = ugs[ugs.cnpj != ""].groupby("cnpj").COUG.apply(lambda s: sorted(set(x.strip() for x in s))).to_dict()
    nome_ug = {r.COUG.strip(): r.NOUG.strip() for r in ugs.itertuples()}
    nome_ug["240201"] = "EMPRESA BRASILIENSE DE TURISMO"
    eq = pd.read_csv(RAIZ / "extracao_pontual" / "equivalencias_cnpj_ug.csv", dtype=str, sep=";").fillna("")
    equiv = {r.cnpj.lstrip("0"): [u for u in r.ug.split("|") if u] for r in eq.itertuples() if r.status in ("confirmada", "sugerida")}

    def ug_do_registro(r):
        reg = r["COUG"]
        if reg not in ("", "0", "130101"):
            return reg
        ben = r["COBENEFICIADO"]
        if re.fullmatch(r"\d{6}-\d{5}", ben):
            return ben[:6]
        u = por_cnpj.get(dig(ben).lstrip("0"), [])
        return u[0] if len(u) == 1 else reg

    ABREV = {"SEC": "SECRETARIA", "EST": "ESTADO", "DESENV": "DESENVOLVIMENTO", "BOMB": "BOMBEIROS", "DIST": "DISTRITO", "DF": "DISTRITO FEDERAL",
             "HABIT": "HABITACAO", "AGRIC": "AGRICULTURA", "ADM": "ADMINISTRACAO"}
    VAZIAS = {"DE", "DO", "DA", "DOS", "DAS", "E", "DISTRITO", "FEDERAL", "ESTADO", "SECRETARIA", "FUNDO", "FUNDACAO", "COMPANHIA", "EMPRESA"}

    def tokens(nome):
        t = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode().upper()
        t = re.sub(r"[^A-Z0-9 ]", " ", t)
        return {w for x in t.split() for w in ABREV.get(x, x).split() if w not in VAZIAS}

    tok_ug = {c: tokens(n) for c, n in nome_ug.items()}

    def melhor_nome(tn, pool, minimo):
        pontos = sorted(((len(tn & tok_ug[c]) / len(tn | tok_ug[c]) if tok_ug.get(c) else 0, c) for c in pool), reverse=True)
        if pontos and pontos[0][0] >= minimo and pontos[0][0] > 0 and (len(pontos) < 2 or pontos[0][0] > pontos[1][0]):
            return pontos[0][1]
        return ""

    def ug_por_nome(nome, pool):
        tn = tokens(nome)
        if not tn:
            return ""
        return (melhor_nome(tn, pool, 0.0) if pool else "") or melhor_nome(tn, list(tok_ug), 0.6)

    idx_a, idx_b, idx_p = {}, {}, {}
    for nt, r in siggo.iterrows():
        a = dig(r["NUTRANSFSIAFI"])
        if a.strip("0"):
            idx_a.setdefault(a, []).append(nt)
        for k in {dig(r["NUORIGINAL"])} | set(re.findall(r"\d{6,}", re.sub(r"(?<=\d)\.(?=\d)", "", r["NUORIGINAL"]))):
            if k.strip("0"):
                idx_b.setdefault(k, []).append(nt)
        k = norm_prop(r["NUORIGINAL"])
        if k:
            idx_p.setdefault(k, []).append(nt)

    inst = []
    for u in universo:
        ident, fonte = u["ident"], u["fonte"]
        s_ref = sic.get(g.chave_id(ident), {}) if fonte == "Discricionárias e Legais" else {}
        sit = u["situacao"]
        if fonte == "Discricionárias e Legais":
            celeb = s_ref.get("IND_ASSINADO", "") == "SIM"
        elif fonte == "Gestão de Parcerias":
            celeb = sit == "Aprovada"
        elif fonte == "Transferências Especiais":
            celeb = sit == "CIENTE"
        else:
            celeb = sit == "AUTORIZADO"
        sit_tg = s_ref.get("SIT_CONVENIO") or sit
        desemb = gu.num(s_ref.get("VL_DESEMBOLSADO_CONV", "")) or 0.0
        e_baixa = any(b in sit_tg.upper() for b in BAIXAS)
        chave = dig(ident)
        alfa = fonte == "Discricionárias e Legais" and not chave.isdigit()
        ra = [] if alfa else idx_a.get(chave, [])
        rb = [] if alfa else [x for x in idx_b.get(chave, []) if x not in ra]
        d = det[fonte].get(g.chave_id(ident), {})
        cnpj = (d.get("cnpj") or "").lstrip("0")
        cand = por_cnpj.get(cnpj, []) or equiv.get(cnpj, [])
        if len(cand) != 1:   # CNPJ compartilhado ou sem UG: desempata pelo nome do beneficiario/executor/fundo
            achada = ug_por_nome(d.get("benef") or u["benef"], cand)
            if achada:
                cand = [achada]
        prop_n = norm_prop(s_ref.get("NR_PROPOSTA", "")) if s_ref else ""
        rp = []
        if prop_n and not ra and not rb:   # nº da proposta: so sem registro pela chave principal, registro unico e UG coerente
            cands_p = [x for x in idx_p.get(prop_n, []) if (not cand or ug_do_registro(siggo.loc[x]) in cand)]
            if len(cands_p) == 1:
                rp = cands_p
                rb = rb + rp
        inst.append(dict(u=u, ident=ident, fonte=fonte, s_ref=s_ref, d=d, celeb=celeb, sit_tg=sit_tg, desemb=desemb, e_baixa=e_baixa,
                         baixa_sem_repasse=e_baixa and desemb == 0, chave=chave, alfa=alfa, ra=ra, rb=rb, rp=rp, prop=prop_n, cnpj=cnpj, cand=cand))
    usados = {x for i in inst for x in i["ra"] + i["rb"]}

    def pistas(i, ug):
        if not ug:
            return []
        s_ref, u, d = i["s_ref"], i["u"], i["d"]
        vg = u["valor_global"]
        vrep = gu.num(s_ref.get("VL_REPASSE_CONV", "")) if s_ref else None
        proc = dig(s_ref.get("NR_PROCESSO", "")) if s_ref else dig(sei.get(g.chave_id(i["ident"]), ""))
        conta = gp.dig(s_ref.get("CD_CONTA", "")) if s_ref else ""
        ini = d.get("ini")
        res = []
        for nt, r in siggo.iterrows():
            if nt in usados or ug not in (ug_do_registro(r), r["COBENEFICIADO"][:6]):
                continue
            sinais, p_s = [], dig(r["NUPROCESSO"])
            if len(proc.lstrip("0")) >= 9 and len(p_s.lstrip("0")) >= 9 and (proc.lstrip("0") in p_s or p_s.lstrip("0") in proc):
                sinais.append("processo coincide")
            v = gu.num(r["VATRANSFERENCIA"]) or 0
            if vg and (abs(v - vg) < 0.01 or (vrep and abs(v - vrep) < 0.01)):
                sinais.append("valor igual")
            c = gp.dig(r["NUCONTA"])
            if conta and c and len(c) >= 5 and (c == conta or (len(conta) >= 6 and len(c) >= 6 and (conta.endswith(c) or c.endswith(conta)))):
                sinais.append("conta igual")
            esp = {"Transferências Especiais": "10", "Fundo a Fundo": "12"}.get(i["fonte"]) or ("13" if s_ref.get("MODALIDADE", "").upper() == "CONTRATO DE REPASSE" else "")
            if esp and r["INESPECIE"].lstrip("0") == esp:
                sinais.append("espécie igual")
            ds = gu.d_sig(r["DAINIVIGENCIA"])
            if ini and ds and ds == ini.date():
                sinais.append("início de vigência igual")
            if "processo coincide" in sinais or len(sinais) >= 2:
                res.append((nt, sinais))
        res.sort(key=lambda x: (-len(x[1]), x[0]))
        return res[:3]

    linhas, reverso = [], {}
    for i in inst:
        u, d, fonte, ident = i["u"], i["d"], i["fonte"], i["ident"]
        regs = i["ra"] + i["rb"]
        if len(i["cand"]) == 1:
            ug_api, modo = i["cand"][0], "unica"
        elif len(i["cand"]) > 1:
            ug_api, modo = "", "compartilhado"
        else:
            ug_api, modo = "", "sem_ug"
        situ = i["sit_tg"] or "Sem situação"
        base = dict(fonte=fonte, ident=ident, benef=d.get("benef") or gu.titulo(u["benef"]), data=u["data"], situacao_api=situ,
                    execucao="Com" if u["valor_repassado"] > 0 else "Sem", valor_exec=u["valor_repassado"],
                    modalidade=d.get("mod") or fonte, concedente=u["concedente"], ini=fmt_dt(d.get("ini")), fim=fmt_dt(d.get("fim")), valor_global=u["valor_global"])

        def rotulo(ug):
            if ug:
                return f"{ug} – {nome_ug.get(ug, '')}".strip(" –")
            return SEM_UG

        grupo_sem_cadastro = BAIXA if i["e_baixa"] else ANTERIOR
        if regs:
            for nt in regs:
                r = siggo.loc[nt]
                ug = ug_api or ug_do_registro(r)
                tipo_chave = "Nº TransfereGov" if nt in i["ra"] else ("Nº da proposta" if nt in i["rp"] else "Nº Original")
                if not i["celeb"]:
                    linhas.append(dict(base, ug=rotulo(ug), nt=nt, chave_tipo=tipo_chave, grupo=grupo_sem_cadastro, status=situ,
                                       texto=f"Instrumento ainda não celebrado ou encerrado sem repasse no TransfereGov (situação: {situ}); sem exigência de adequação."))
                    reverso.setdefault(nt, []).append((ident, fonte, grupo_sem_cadastro, tipo_chave))
                    continue
                L = gu.checar(r, ident, fonte, i["s_ref"], dict(u, **{"Valor Global (R$)": u["valor_global"]}))
                itens = [(x[0], x[1], x[2], x[3], x[5]) for x in L if x[4] == "Ajustar"]
                if fonte in ("Transferências Especiais", "Fundo a Fundo"):
                    itens = [x for x in itens if x[0] != "Número Original"]
                if fonte == "Transferências Especiais":
                    emenda = emenda_plano.get(g.chave_id(ident), "")
                    if emenda and dig(r["NUORIGINAL"]) != emenda:
                        itens.append(("Número Original", "Art. 6º, III, “a”", repr(r["NUORIGINAL"]), f"{emenda} (código da emenda)", ""))
                    for nome_c, col, ref in (("Data Início Vigência", "DAINIVIGENCIA", d.get("ini")), ("Data Fim Vigência", "DAFIMVIGENCIA", d.get("fim"))):
                        a = gu.d_sig(r[col])
                        if ref is not None and a and a != ref.date():
                            itens.append((nome_c, "Art. 5º, §11", gu.fd(a), gu.fd(ref.date()), ""))
                if nt in i["rb"] and nt not in i["rp"] and not any(x[0] == "Nº TransfereGov" for x in itens):
                    itens.insert(0, ("Nº TransfereGov", "Art. 5º, §§1º e 2º", repr(r["NUTRANSFSIAFI"]) if r["NUTRANSFSIAFI"] else "em branco", i["chave"], ""))
                textos = [gu.linha_texto(*x) for x in itens]
                if nt in i["rp"]:
                    textos.append(f"Nota: o Número Original registrado corresponde ao número da proposta SICONV ({i['prop']}); o identificador do instrumento é o convênio {i['chave']}.")
                if len(regs) > 1:
                    textos.append(f"Atenção: o instrumento aparece em {len(regs)} transferências do SIGGo ({', '.join(regs)}): verificar possível duplicidade.")
                outra = bool(ug_api and ug_do_registro(r) != ug_api)
                if outra:
                    textos.append(f"Atenção: a transferência está registrada na UG {ug_do_registro(r)}, mas pela API o beneficiário/executor é da UG {ug_api}. Confirmar se o registro corresponde a este instrumento.")
                grupo = INCONSISTENTE if (itens or outra) else ADEQUADO
                linhas.append(dict(base, ug=rotulo(ug), nt=nt, chave_tipo=tipo_chave, grupo=grupo, status=grupo,
                                   texto="\n".join(textos) or "Sem divergências apuradas nos dispositivos verificados."))
                reverso.setdefault(nt, []).append((ident, fonte, grupo, tipo_chave))
        else:
            if not i["celeb"]:
                grupo, texto = grupo_sem_cadastro, f"Instrumento ainda não celebrado ou encerrado sem repasse no TransfereGov (situação: {situ}); sem exigência de cadastro."
                status = situ
            elif i["baixa_sem_repasse"]:
                grupo, status = BAIXA, situ
                texto = f"Instrumento com situação “{situ}” e sem repasse no TransfereGov; cadastro não exigido (a UG deve apenas confirmar)."
            else:
                grupo, status = NAO_LOC, NAO_LOC
                chave_txt = "(deixar em branco: código alfanumérico; informar no Número Original)" if i["alfa"] else i["chave"]
                texto = (f"O identificador {ident} não consta no Nº TransfereGov nem no Número Original de nenhuma transferência do SIGGo. "
                         f"Indicar a transferência correspondente e informar o Nº TransfereGov {chave_txt}; se não houver registro, cadastrar a transferência (AFC), conforme IN nº 13/2026. "
                         f"Situação no TransfereGov: {situ}; valor global {gu.brl(u['valor_global'])}; desembolsado {gu.brl(i['desemb'])}.")
                ps = pistas(i, ug_api)
                if ps:
                    texto += "\nPista a confirmar pela UG (não é vínculo): " + "; ".join(f"SIGGo nº {nt} ({', '.join(s)})" for nt, s in ps) + "."
            linhas.append(dict(base, ug=rotulo(ug_api), nt="", chave_tipo="—", grupo=grupo, status=status, texto=texto))
    return pd.DataFrame(linhas), reverso


# --------------------------------------------------------------------------- tabelas
COLS_API_TELA = ["Origem", "Identificador", "Nº Transf. SIGGo", "Beneficiário", "Situação", "Valor (R$)", "Cadastro no SIGGo"]
COLS_API_CSV = ["Modalidade", "Concedente", "Início da vigência", "Fim da vigência", "Valor global (R$)", "Chave de identificação", "Inconsistências (SIGGo × TransfereGov) e providência"]


def tabela_api(linhas):
    df = pd.DataFrame({
        "Origem": linhas["fonte"], "Identificador": linhas["ident"], "Beneficiário": linhas["ug"], "Data de celebração": linhas["data"],
        "Situação": linhas["situacao_api"], "Execução": linhas["execucao"], "Valor (R$)": linhas["valor_exec"], "Beneficiário (API)": linhas["benef"],
        "Nº Transf. SIGGo": linhas["nt"], "Cadastro no SIGGo": linhas["status"],
        "Modalidade": linhas["modalidade"], "Concedente": linhas["concedente"], "Início da vigência": linhas["ini"], "Fim da vigência": linhas["fim"],
        "Valor global (R$)": linhas["valor_global"], "Chave de identificação": linhas["chave_tipo"],
        "Inconsistências (SIGGo × TransfereGov) e providência": linhas["texto"], "_grupo": linhas["grupo"]})
    df["_g"] = df["_grupo"].map(GRAVIDADE)
    df = df.sort_values(["Beneficiário", "_g", "Identificador"]).drop(columns="_g").reset_index(drop=True)
    params = {
        "Identificador": "nº do convênio (SICONV), nº da parceria ou código do plano de ação",
        "Situação": "situação do instrumento na API do TransfereGov",
        "Execução": "Com/Sem valor pago, repassado ou desembolsado na origem",
        "Valor (R$)": "valor pago/repassado/desembolsado na origem (0 = ainda sem execução financeira)",
        "UG": "UG do beneficiário/executor na API (CNPJ → UG); CNPJ de várias UGs usa a UG do registro do SIGGo",
        "Nº Transf. SIGGo": "transferência do SIGGo que traz o identificador (Nº TransfereGov, Nº Original exato ou nº da proposta)",
        "Cadastro no SIGGo": "Adequado · Inconsistente · Não localizado; nos demais, a própria situação do instrumento (Anulado, Cancelado, etc.). Detalhes no CSV",
    }
    return df, {}


def agrupar_instrumentos(api):
    """Uma linha por instrumento, com o pior grupo entre os seus registros e se tem registro no SIGGo."""
    t = api.assign(_g=api["_grupo"].map(GRAVIDADE), _reg=api["Nº Transf. SIGGo"] != "").sort_values("_g")
    out = t.drop_duplicates(["Origem", "Identificador"]).copy()
    out["_reg"] = t.groupby(["Origem", "Identificador"])["_reg"].max().reindex(list(zip(out["Origem"], out["Identificador"]))).values
    return out


def tabela_ug(api):
    inst = agrupar_instrumentos(api)
    linhas = []
    for ug, d in inst.groupby("Beneficiário"):
        c = d["_grupo"].value_counts()
        linhas.append({"UG": ug, "Instrumentos": len(d), "Pendentes": int(c.get(INCONSISTENTE, 0) + c.get(NAO_LOC, 0)),
                       "Inconsistente": int(c.get(INCONSISTENTE, 0)), "Não localizado": int(c.get(NAO_LOC, 0)), "Adequado": int(c.get(ADEQUADO, 0)),
                       "Anulado, cancelado, etc.": int(c.get(BAIXA, 0)), "Não celebrado": int(c.get(ANTERIOR, 0))})
    df = pd.DataFrame(linhas)
    df["a_definir"] = df["UG"].str.startswith("UG NÃO")
    df = df.sort_values(["a_definir", "Pendentes", "Instrumentos"], ascending=[True, False, False]).drop(columns="a_definir").reset_index(drop=True)
    params = {"Instrumentos": "instrumentos encontrados no TransfereGov atribuídos à UG",
              "Pendentes": "Inconsistente + Não localizado: instrumentos celebrados em que a UG precisa agir",
              "Anulado, cancelado, etc.": "anulado, cancelado, rescindido, impedido ou rejeitado (sem exigência de cadastro)",
              "Não celebrado": "proposta, análise ou elaboração: ainda sem celebração"}
    return df, params


def tabela_cz_siggo(siggo_tab, reverso):
    """Mesmas colunas e ordem do antigo 'Cruzamento'; identificacao so pela chave."""
    rastros = {}
    t = siggo_tab.copy()
    t["_nt"] = t["Nº Transferência"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    enc, conf, ind, sug = [], [], [], []
    for _, r in t.iterrows():
        lst = reverso.get(r["_nt"], [])
        if not lst:
            enc.append("-"); conf.append("-"); ind.append(""); sug.append("")
            continue
        melhor = min(lst, key=lambda x: TIER[x[3]])
        pior_tipo = TIER[melhor[3]]
        enc.append(" · ".join(sorted({x[1] for x in lst})))
        conf.append(pior_tipo)
        ind.append(" · ".join(sorted({x[0] for x in lst})))
        if melhor[3] == "Nº TransfereGov":
            sug.append("")
        else:
            valor = re.sub(r"[\s\-\.]", "", melhor[0])
            if str(r["Nº TransfereGov"]).strip():
                valor += " (campo já tem outro valor)"
            sug.append(valor)
    out = pd.DataFrame({
        "Nº Transf.": t["Nº Transferência"], "Espécie": t["Espécie"], "Concedente": t["Concedente"], "Beneficiário": t["Beneficiário"],
        "Objeto": t["Objeto"].astype(str).str.slice(0, 100), "Data Celeb.": t["Data de celebração"], "Valor (R$)": t["Valor transferência (R$)"],
        "Nº TransfereGov": t["Nº TransfereGov"], "Nº Original": t["Nº Original"], "Encontrado em": enc, "Confiança": conf, "Indicador": ind,
        "Sugestão p/ NUTRANSFSIAFI": sug})
    params = {
        "Nº Transf.": "TRANSFERENCIA.NUTRANSFERENCIA", "Espécie": "TRANSFERENCIA.INESPECIE", "Concedente": "TRANSFERENCIA.COCONCENTE",
        "Beneficiário": "TRANSFERENCIA.COBENEFICIADO + UNIDADEGESTORA.NOUG", "Objeto": "TRANSFERENCIA.TXOBJETORESUMIDO",
        "Data Celeb.": "TRANSFERENCIA.DACELEBRACAO", "Valor (R$)": "TRANSFERENCIA.VATRANSFERENCIA",
        "Nº TransfereGov": "TRANSFERENCIA.NUTRANSFSIAFI", "Nº Original": "TRANSFERENCIA.NUORIGINAL",
        "Encontrado em": "fonte do TransfereGov do instrumento que o registro identifica pela chave",
        "Confiança": "chave que identificou o registro: 1 Nº TransfereGov · 2 Nº Original exato · 3 nº da proposta (SICONV); “-” = sem chave",
        "Indicador": "identificador do instrumento: NR_CONVENIO / cd_parceria / codigo_plano_acao, conforme a fonte",
        "Sugestão p/ NUTRANSFSIAFI": "valor que a UG deve cadastrar no Nº TransfereGov: identificador do instrumento, sem hífen e sem pontos",
    }
    return out, params


def tabela_html(df, aba_id, params, exibir=None):
    """Igual a g.montar_tabela_html, mas a tela mostra so 'exibir'; o CSV traz todas as colunas do df."""
    colunas = list(df.columns) if exibir is None else list(exibir)
    todas = [c for c in df.columns if not c.startswith("_")]
    registros = df.fillna("").to_dict(orient="records")
    for r in registros:
        for c in todas:
            if ("Valor" in c or "valor" in c) and r[c] != "":
                r[c] = g.fmt_valor(r[c])
        for c in [k for k in r if k.startswith("_")]:
            del r[c]

    def cab(c, i):
        classe = "num" if ("Valor" in c or "valor" in c) else ""
        sub = f'<span class="acct">{params.get(c, "")}</span>' if params.get(c) else ""
        return f'<th class="{classe}" onclick="ordenar(\'{aba_id}\',{i})">{c} <span id="si-{aba_id}-{i}" class="si">⇅</span>{sub}</th>'

    thead = "".join(cab(c, i) for i, c in enumerate(colunas))
    return f"""
    <div class="tsec"><div class="tw"><table id="tabela-{aba_id}"><thead><tr>{thead}</tr></thead><tbody></tbody></table></div></div>
    <script>
      DADOS['{aba_id}'] = {json.dumps(registros, ensure_ascii=False)};
      COLUNAS['{aba_id}'] = {json.dumps(colunas, ensure_ascii=False)};
      COLUNAS_CSV['{aba_id}'] = {json.dumps(todas, ensure_ascii=False)};
      COLUNAS_NUM['{aba_id}'] = {json.dumps(["Valor" in c or "valor" in c for c in colunas])};
    </script>"""


# --------------------------------------------------------------------------- html
ABAS = [
    {"id": "parcerias", "titulo": "Gestão de Parcerias", "icone": "🤝"},
    {"id": "especiais", "titulo": "Transferências Especiais", "icone": "🏛️"},
    {"id": "fundoafundo", "titulo": "Fundo a Fundo", "icone": "💰"},
    {"id": "siconv", "titulo": "Discricionárias e Legais", "icone": "📄"},
    {"id": "siggo", "titulo": "SIGGO", "icone": "🗄️"},
    {"id": "cz_siggo", "titulo": "Cruzamento ótica SIGGo", "icone": "🔁"},
    {"id": "cz_api", "titulo": "Cruzamento ótica TransfereGov", "icone": "🔗"},
    {"id": "resumo", "titulo": "Resumo", "icone": "📊"},
]
FONTES_DESCR = {
    "parcerias": "API /parcerias — endpoints /proposta + /parceria + /programa + /documento-habil + /ordem-pagamento",
    "especiais": "API /especiais — endpoints /beneficiarios-especiais + /planos-acao-especiais + /programas-especiais + cadeia empenho→documento hábil→ordem de pagamento",
    "fundoafundo": "API /fundoafundo — endpoint /planos-acao + cadeia dados bancários→lançamentos→subtransações",
    "siconv": "Download CSV “Transferências Discricionárias e Legais” (Transferegov) — siconv_convenio + siconv_proposta + siconv_emenda: convênios, contratos de repasse e termos de compromisso",
    "siggo": "Oracle SIGGO — MIL2026.TRANSFERENCIA + MIL2026.UNIDADEGESTORA",
    "cz_siggo": "SIGGO (MIL2026.TRANSFERENCIA) cruzado com as 4 fontes do TransfereGov pela chave: Nº TransfereGov, Nº Original (exato) ou nº da proposta",
    "cz_api": "Instrumentos encontrados nas 4 fontes do TransfereGov, cada um com o seu registro no SIGGO (quando houver) — o detalhamento das inconsistências está no CSV",
    "ug": "Instrumentos encontrados no TransfereGov por UG, a partir da aba Cruzamento ótica TransfereGov",
}


def barra(label, pct, texto, cor):
    return (f'<div class="resumo-bar-row"><div class="resumo-bar-label" title="{label}">{label}</div>'
            f'<div class="resumo-bar-track"><div class="resumo-bar-fill" style="width:{pct:.1f}%;background:{cor}"></div></div>'
            f'<div class="resumo-bar-value">{texto}</div></div>')


def cor_cobertura(pct):   # logica do painel anterior: verde >= 50%, amarelo 25-49%, vermelho < 25%
    return "#1a7a44" if pct >= 50 else ("#f0a500" if pct >= 25 else "#c0392b")


def resumo_html(api, ug_df, ug_params):
    inst = agrupar_instrumentos(api)
    total = len(inst)
    barras_status = "".join(
        barra(ROTULO_GRUPO[gr], (n / total * 100) if total else 0, f"{fmt_int(n)} ({n / total * 100:.1f}%)".replace(".", ",") if total else "0", CORES_GRUPO[gr])
        for gr, n in [(gr, int((inst["_grupo"] == gr).sum())) for gr in (ADEQUADO, INCONSISTENTE, NAO_LOC, BAIXA, ANTERIOR)])
    cards = ""
    for fonte in g.FONTE_TAB_INFO:
        p = inst[inst["Origem"] == fonte]
        n, achados = len(p), int(p["_reg"].sum())
        pc = (achados / n * 100) if n else 0
        cor = cor_cobertura(pc)
        cards += (f'<div class="resumo-cov-card"><div class="kl">{fonte}</div><div class="resumo-cov-pct" style="color:{cor}">{pc:.0f}%</div>'
                  f'<div class="resumo-cov-track"><div class="resumo-cov-fill" style="width:{pc:.1f}%;background:{cor}"></div></div>'
                  f'<div class="resumo-cov-sub">{fmt_int(achados)} de {fmt_int(n)} localizados no SIGGO</div></div>')
    return f"""
    <div class="resumo-widgets">
      <div class="resumo-widget-card"><h3 class="resumo-titulo">Situação do cadastro — {fmt_int(total)} instrumentos encontrados no TransfereGov</h3><div class="resumo-bars">{barras_status}</div></div>
    </div>
    <h3 class="resumo-titulo">Cobertura por fonte — instrumentos com o identificador cadastrado no NUTRANSFSIAFI do SIGGO</h3>
    <div class="resumo-cov-grid">{cards}</div>
    <h3 class="resumo-titulo">Instrumentos encontrados no TransfereGov por UG</h3>
    {filtros_ug(len(ug_df))}
    {tabela_html(ug_df, "ug", ug_params)}
    """


def filtros_ug(n):
    return f"""
    <div class="fbar">
      <div class="fg"><label>Buscar UG</label><input type="text" id="fl-ug" placeholder="Código ou nome da UG…" oninput="aplicarFiltrosUg()"></div>
      <div class="fg"><label>&nbsp;</label><span class="contador">Clique no nome da UG para abrir os instrumentos dela.</span></div>
      <div class="bgrp">
        <button class="btn btn-g" onclick="limparFiltrosUg()">↺ Limpar filtros</button>
        <button class="btn btn-t" onclick="exportarCSV('ug')">⬇ Baixar CSV</button>
        <span class="contador" id="contador-ug">{n} registros</span>
      </div>
    </div>"""


def filtros_api(api):
    ugs = sorted(api["Beneficiário"].unique(), key=lambda x: (x.startswith("UG NÃO"), x))
    opcoes = lambda col: "".join(f'<option value="{v}">{v}</option>' for v in sorted(v for v in api[col].unique() if v))
    dl = "".join(f'<option value="{u}">' for u in ugs)
    return f"""
    <div class="fbar">
      <div class="fg"><label>Beneficiário (UG)</label><input type="text" id="fug-cz_api" list="lista-ugs" placeholder="Código ou nome da UG…" oninput="aplicarFiltrosApi()"><datalist id="lista-ugs">{dl}</datalist></div>
      <div class="fg"><label>Origem</label><select id="ffonte-cz_api" onchange="aplicarFiltrosApi()"><option value="">Todas</option>{opcoes("Origem")}</select></div>
      <div class="fg"><label>Cadastro no SIGGO</label><select id="fcad-cz_api" onchange="aplicarFiltrosApi()"><option value="">Todos</option>{opcoes("Cadastro no SIGGo")}</select></div>
      <div class="fg"><label>Busca livre</label><input type="text" id="fl-cz_api" placeholder="Qualquer campo…" oninput="aplicarFiltrosApi()"></div>
      <div class="bgrp">
        <button class="btn btn-g" onclick="limparFiltrosApi()">↺ Limpar filtros</button>
        <button class="btn btn-t" onclick="exportarCSV('cz_api')">⬇ Baixar CSV</button>
        <span class="contador" id="contador-cz_api">{len(api)} registros</span>
      </div>
    </div>"""


def filtros_cz_siggo(df):
    esp = "".join(f'<option value="{e}">{e}</option>' for e in sorted(e for e in df["Espécie"].dropna().unique() if e))
    fonte = "".join(f'<option value="{e}">{e}</option>' for e in sorted(e for e in df["Encontrado em"].dropna().unique() if e))
    return f"""
    <div class="fbar">
      <div class="fg"><label>Espécie</label><select id="fesp-cz_siggo" onchange="aplicarFiltrosCzSiggo()"><option value="">Todas</option>{esp}</select></div>
      <div class="fg"><label>Beneficiário</label><input type="text" id="fben-cz_siggo" placeholder="Nome ou código da UG…" oninput="aplicarFiltrosCzSiggo()"></div>
      <div class="fg"><label>Encontrado em</label><select id="ffonte-cz_siggo" onchange="aplicarFiltrosCzSiggo()"><option value="">Todas</option>{fonte}</select></div>
      <div class="fg"><label>Busca livre</label><input type="text" id="fl-cz_siggo" placeholder="Qualquer campo…" oninput="aplicarFiltrosCzSiggo()"></div>
      <div class="bgrp">
        <button class="btn btn-g" onclick="limparFiltrosCzSiggo()">↺ Limpar filtros</button>
        <button class="btn btn-t" onclick="exportarCSV('cz_siggo')">⬇ Baixar CSV</button>
        <span class="contador" id="contador-cz_siggo">{len(df)} registros</span>
      </div>
    </div>"""


def filtros_siggo(df):
    esp = "".join(f'<option value="{e}">{e}</option>' for e in sorted(e for e in df["Espécie"].dropna().unique() if e))
    return f"""
    <div class="fbar">
      <div class="fg"><label>Espécie</label><select id="fesp-siggo" onchange="aplicarFiltrosSiggo()"><option value="">Todas</option>{esp}</select></div>
      <div class="fg"><label>Nº Transferência</label><input type="text" id="fnt-siggo" placeholder="Nº transferência…" oninput="aplicarFiltrosSiggo()"></div>
      <div class="fg"><label>Concedente</label><input type="text" id="fcon-siggo" placeholder="CNPJ do concedente…" oninput="aplicarFiltrosSiggo()"></div>
      <div class="fg"><label>Beneficiário</label><input type="text" id="fben-siggo" placeholder="Nome ou código da UG…" oninput="aplicarFiltrosSiggo()"></div>
      <div class="fg"><label>Nº TransfereGov</label><input type="text" id="fsia-siggo" placeholder="Nº TransfereGov…" oninput="aplicarFiltrosSiggo()"></div>
      <div class="fg"><label>Busca livre</label><input type="text" id="fl-siggo" placeholder="Qualquer campo…" oninput="aplicarFiltrosSiggo()"></div>
      <div class="bgrp">
        <button class="btn btn-g" onclick="limparFiltrosSiggo()">↺ Limpar filtros</button>
        <span class="contador" id="contador-siggo">{len(df)} registros</span>
      </div>
    </div>"""


def aviso_html(n_siggo_total, n_siggo_vis, fallback_especiais):
    extra = ("<li><strong>Atenção:</strong> a API de Transferências Especiais está sem dados; a aba usa reconstrução temporária (painel de 16/10/2026), "
             "substituída automaticamente quando a API voltar.</li>") if fallback_especiais else ""
    return f"""
<div class="aviso">
  <span>ℹ️</span>
  <details>
    <summary>Como ler este painel — escopo, chave de identificação e situações do cadastro</summary>
    <ul>
      <li><strong>Escopo:</strong> instrumentos de órgãos e entidades do GDF (CNPJ da lista de UGs do SIGGO + ente Distrito Federal) nas quatro fontes do TransfereGov:
        Discricionárias e Legais (convênios, contratos de repasse e termos de compromisso), Gestão de Parcerias, Transferências Especiais e Fundo a Fundo.
        Excluídos: Termo de Fomento e entidades privadas apenas sediadas em Brasília. Traz recursos com ou sem emenda parlamentar.</li>
      <li><strong>Chave de identificação:</strong> o identificador do instrumento na API (nº do convênio, nº da parceria ou código do plano de ação). O instrumento tem registro no SIGGO quando esse
        identificador consta no <em>Nº TransfereGov</em> (<code>NUTRANSFSIAFI</code>) ou, de forma exata, no <em>Nº Original</em> (campo inteiro ou número isolado, como 888228/2019); no SICONV vale também o nº da
        proposta, se apontar um único registro da mesma UG. Conta, valor, objeto, processo e semelhança de nome não identificam: aparecem só como “pista a confirmar” no CSV.</li>
      <li><strong>Cruzamento ótica TransfereGov:</strong> parte dos instrumentos encontrados no TransfereGov. <em>Cadastro no SIGGO</em>: <em>Adequado</em>; <em>Inconsistente</em> (há ajuste a fazer, descrito no CSV conforme a IN nº 13/2026);
        <em>Não localizado</em> (instrumento celebrado sem registro: a UG indica a transferência ou cadastra); nos demais, a situação do próprio instrumento (Anulado, Cancelado, Rescindido, Impedido, Rejeitado ou ainda não celebrado), sem exigência de cadastro.</li>
      <li><strong>Cruzamento ótica SIGGo:</strong> parte dos {fmt_int(n_siggo_vis)} registros do SIGGO em que o concedente não é o GDF e o beneficiário é o GDF (de {fmt_int(n_siggo_total)} na tabela) e indica o instrumento que cada um identifica pela chave.</li>
      <li><strong>UG do instrumento:</strong> pela UG do CNPJ do beneficiário/executor (com lista de equivalências); se o CNPJ é de várias UGs, vale a UG do registro do SIGGO; sem registro, “A definir”.</li>
      <li><strong>Valor pago:</strong> regras do setor de Transparência/Governo Aberto do DF, por fonte (cadeia de tabelas); no Fundo a Fundo, contas compartilhadas têm o pago rateado pelo valor do repasse.</li>
      <li><strong>Limites:</strong> Termos de Execução Descentralizada (TED) ocorrem só entre órgãos da União e não entram; instrumentos fora do Transferegov não são cobertos. Fonte: <code>https://api-publica.transferegov.gestao.gov.br/</code>.</li>
      {extra}
    </ul>
  </details>
</div>"""


CSS_EXTRA = """
.aviso{background:#fff8e6;border-bottom:1px solid #f0c060;padding:8px 28px;font-size:11.5px;color:#7a5c00;display:flex;align-items:flex-start;gap:8px;line-height:1.5}
.aviso details{flex:1}.aviso summary{cursor:pointer;font-weight:600}.aviso ul{margin:6px 0 2px 18px}.aviso li{margin-bottom:4px}
.aviso code{background:#fff;padding:1px 5px;border-radius:4px;font-size:10.5px}
.btn-t{background:var(--teal);color:#fff}
td.larga{max-width:380px;min-width:200px}
a.lk{color:var(--teal);font-weight:600;text-decoration:none}a.lk:hover{text-decoration:underline}
.st{display:inline-block;padding:2px 9px;border-radius:12px;font-size:10.5px;font-weight:700;color:#fff}
.st-ok{background:#1a7a44}.st-warn{background:#d98e00}.st-bad{background:#c0392b}.st-na{background:#8a97b0}
.legenda-cor{font-size:10.5px;color:var(--muted);margin-top:12px}
"""

JS_COMUM = r"""
const DADOS = window.DADOS, COLUNAS = window.COLUNAS, COLUNAS_NUM = window.COLUNAS_NUM, COLUNAS_CSV = window.COLUNAS_CSV, ORDEM = window.ORDEM, LINHAS_ATUAIS = window.LINHAS_ATUAIS;
const COLUNAS_STATUS = {'cz_api': ['Cadastro no SIGGo']};
const COLUNAS_LINK = {'ug': ['UG'], 'resumo_ug': ['UG']};
const COLUNAS_LARGAS = ['UG', 'Beneficiário', 'Concedente', 'Objeto'];
const CLASSE_STATUS = {'Adequado':'st-ok','Inconsistente':'st-warn','Não localizado':'st-bad'};
const SUBTABELAS_RESUMO = ['ug'];

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }

function mostrarAba(id) {
  document.querySelectorAll('.aba-conteudo').forEach(el => el.classList.remove('ativo'));
  document.querySelectorAll('.aba-btn').forEach(el => el.classList.remove('ativo'));
  document.getElementById('conteudo-' + id).classList.add('ativo');
  document.querySelector(`.aba-btn[data-aba="${id}"]`).classList.add('ativo');
  if (id === 'resumo') { SUBTABELAS_RESUMO.forEach(sub => { if (!ORDEM[sub]) renderizar(sub, DADOS[sub]); }); return; }
  if (!ORDEM[id]) renderizar(id, DADOS[id]);
}

function renderizar(id, linhas) {
  LINHAS_ATUAIS[id] = linhas;
  const tbody = document.querySelector(`#tabela-${id} tbody`);
  const cols = COLUNAS[id], numCols = COLUNAS_NUM[id] || [];
  const status = COLUNAS_STATUS[id] || [], links = COLUNAS_LINK[id] || [];
  tbody.innerHTML = linhas.map(row => '<tr>' + cols.map((c, i) => {
    const v = row[c];
    let conteudo = esc(v), cls = numCols[i] ? 'num' : '';
    if (status.includes(c) && v !== '') conteudo = `<span class="st ${CLASSE_STATUS[v] || 'st-na'}">${esc(v)}</span>`;
    else if (links.includes(c) && v !== '') conteudo = `<a class="lk" href="#" data-ug="${esc(v)}" onclick="verUG(this.dataset.ug);return false">${esc(v)}</a>`;
    if (COLUNAS_LARGAS.includes(c)) cls += ' larga';
    return `<td class="${cls}" title="${esc(v)}">${conteudo}</td>`;
  }).join('') + '</tr>').join('');
  const contador = document.getElementById('contador-' + id);
  if (contador) contador.textContent = linhas.length + ' registros';
}

function aplicarFiltros(id) {
  const ente = (document.getElementById('fe-' + id).value || '').toLowerCase();
  const ano = document.getElementById('fa-' + id).value, sit = document.getElementById('fs-' + id).value;
  const elMod = document.getElementById('fm-' + id), mod = elMod ? elMod.value : '';
  const livre = (document.getElementById('fl-' + id).value || '').toLowerCase();
  renderizar(id, DADOS[id].filter(row => {
    if (ente && !String(row['Beneficiário'] || '').toLowerCase().includes(ente)) return false;
    if (ano && String(row['Ano']) !== ano) return false;
    if (sit && row['Situação'] !== sit) return false;
    if (mod && row['Modalidade'] !== mod) return false;
    if (livre && !Object.values(row).some(v => String(v).toLowerCase().includes(livre))) return false;
    return true;
  }));
}
function limparFiltros(id) {
  ['fe-','fa-','fs-','fm-','fl-'].forEach(p => { const el = document.getElementById(p + id); if (el) el.value = ''; });
  renderizar(id, DADOS[id]);
}

function aplicarFiltrosSiggo() {
  const id = 'siggo', v = k => (document.getElementById(k + '-' + id).value || '').toLowerCase();
  renderizar(id, DADOS[id].filter(row => {
    if (v('fesp') && String(row['Espécie']).toLowerCase() !== v('fesp')) return false;
    if (v('fnt') && !String(row['Nº Transferência'] || '').toLowerCase().includes(v('fnt'))) return false;
    if (v('fcon') && !String(row['Concedente'] || '').toLowerCase().includes(v('fcon'))) return false;
    if (v('fben') && !String(row['Beneficiário'] || '').toLowerCase().includes(v('fben'))) return false;
    if (v('fsia') && !String(row['Nº TransfereGov'] || '').toLowerCase().includes(v('fsia'))) return false;
    if (v('fl') && !Object.values(row).some(x => String(x).toLowerCase().includes(v('fl')))) return false;
    return true;
  }));
}
function limparFiltrosSiggo() { ['fesp','fnt','fcon','fben','fsia','fl'].forEach(p => document.getElementById(p + '-siggo').value = ''); renderizar('siggo', DADOS['siggo']); }

function aplicarFiltrosApi() {
  const id = 'cz_api', v = k => (document.getElementById(k + '-' + id).value || '').toLowerCase();
  renderizar(id, DADOS[id].filter(row => {
    if (v('fug') && !String(row['Beneficiário']).toLowerCase().includes(v('fug'))) return false;
    if (v('ffonte') && String(row['Origem']).toLowerCase() !== v('ffonte')) return false;
    if (v('fcad') && String(row['Cadastro no SIGGo']).toLowerCase() !== v('fcad')) return false;
    if (v('fl') && !Object.values(row).some(x => String(x).toLowerCase().includes(v('fl')))) return false;
    return true;
  }));
}
function limparFiltrosApi() { ['fug','ffonte','fcad','fl'].forEach(p => document.getElementById(p + '-cz_api').value = ''); renderizar('cz_api', DADOS['cz_api']); }

function aplicarFiltrosCzSiggo() {
  const id = 'cz_siggo', v = k => (document.getElementById(k + '-' + id).value || '').toLowerCase();
  renderizar(id, DADOS[id].filter(row => {
    if (v('fesp') && String(row['Espécie']).toLowerCase() !== v('fesp')) return false;
    if (v('ffonte') && String(row['Encontrado em']).toLowerCase() !== v('ffonte')) return false;
    if (v('fben') && !String(row['Beneficiário']).toLowerCase().includes(v('fben'))) return false;
    if (v('fl') && !Object.values(row).some(x => String(x).toLowerCase().includes(v('fl')))) return false;
    return true;
  }));
}
function limparFiltrosCzSiggo() { ['fesp','ffonte','fben','fl'].forEach(p => document.getElementById(p + '-cz_siggo').value = ''); renderizar('cz_siggo', DADOS['cz_siggo']); }

function aplicarFiltrosUg() {
  const t = (document.getElementById('fl-ug').value || '').toLowerCase();
  renderizar('ug', DADOS['ug'].filter(row => !t || String(row['UG']).toLowerCase().includes(t)));
}
function limparFiltrosUg() { document.getElementById('fl-ug').value = ''; renderizar('ug', DADOS['ug']); }

function verUG(nome) {
  mostrarAba('cz_api');
  limparFiltrosApi();
  document.getElementById('fug-cz_api').value = nome;
  aplicarFiltrosApi();
  window.scrollTo({top: 0, behavior: 'smooth'});
}

function exportarCSV(id) {
  const cols = COLUNAS_CSV[id] || COLUNAS[id], linhas = LINHAS_ATUAIS[id] || DADOS[id];
  const q = v => '"' + String(v === undefined ? '' : v).replace(/"/g, '""') + '"';
  const csv = [cols.map(q).join(';')].concat(linhas.map(r => cols.map(c => q(r[c])).join(';'))).join('\r\n');
  const blob = new Blob(['﻿' + csv], {type: 'text/csv;charset=utf-8'});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
  a.download = 'transferegov_' + id + '.csv'; document.body.appendChild(a); a.click(); a.remove();
}

function valorOrdenavel(v) {
  const s = String(v), dm = s.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
  if (dm) return Number(dm[3] + dm[2] + dm[1]);
  const n = parseFloat(s.replace(/\./g,'').replace(',','.'));
  return isNaN(n) ? v : n;
}
function compararOrdenavel(a, b, chave, asc) {
  let va = valorOrdenavel(a[chave]), vb = valorOrdenavel(b[chave]);
  if (typeof va !== typeof vb) { va = String(a[chave]); vb = String(b[chave]); }
  if (va < vb) return asc ? -1 : 1;
  if (va > vb) return asc ? 1 : -1;
  return 0;
}
function ordenar(id, colIdx) {
  const chave = COLUNAS[id][colIdx];
  ORDEM[id] = ORDEM[id] === chave ? null : chave;
  const asc = ORDEM[id] === chave;
  document.querySelectorAll(`#tabela-${id} .si`).forEach(el => el.textContent = '⇅');
  const seta = document.getElementById(`si-${id}-${colIdx}`); if (seta) seta.textContent = asc ? '↑' : '↓';
  renderizar(id, [...(LINHAS_ATUAIS[id] || DADOS[id])].sort((a, b) => compararOrdenavel(a, b, chave, asc)));
}
document.addEventListener('DOMContentLoaded', () => {
  // Como no painel anterior: o cruzamento abre ordenado pela data de celebração mais recente
  const idCruz = 'cz_siggo', chaveData = 'Data Celeb.', idxData = (COLUNAS[idCruz] || []).indexOf(chaveData);
  if (idxData >= 0) {
    ORDEM[idCruz] = chaveData;
    const seta = document.getElementById(`si-${idCruz}-${idxData}`); if (seta) seta.textContent = '↓';
    renderizar(idCruz, [...DADOS[idCruz]].sort((a, b) => compararOrdenavel(a, b, chaveData, false)));
  }
  mostrarAba('resumo');
});
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TransfereGov × GDF — Visão Consolidada</title>
<style>
@@CSS_BASE@@
@@CSS_EXTRA@@
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">🤝</div>
    <h1>TRANSFEREGOV<span>Visão consolidada — somente órgãos/entidades do Distrito Federal</span></h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <span id="ts">Gerado em @@DATA@@</span>
</header>
@@AVISO@@
<nav class="abas-nav">@@NAV@@</nav>
<script>
window.DADOS = {}; window.COLUNAS = {}; window.COLUNAS_NUM = {}; window.COLUNAS_CSV = {}; window.ORDEM = {}; window.LINHAS_ATUAIS = {};
</script>
@@CONTEUDO@@
<script>
@@JS@@
</script>
</body>
</html>
"""


def css_base():
    """CSS do painel atual (copiado do TEMPLATE do gerador em producao, sem o rodape)."""
    t = g.TEMPLATE
    ini = t.index("<style>") + len("<style>")
    fim = t.index("</style>")
    return t[ini:fim].replace("{{", "{").replace("}}", "}")


def main():
    dfs = {"parcerias": g.montar_parcerias(), "especiais": g.montar_especiais(), "fundoafundo": g.montar_fundoafundo(),
           "siconv": g.montar_siconv(), "siggo": g.montar_siggo()}
    siggo_tab, siggo_params = dfs["siggo"]
    siggo_tab = siggo_tab.rename(columns={"Nº SICONV/SIAFI": "Nº TransfereGov"})
    siggo_params = {("Nº TransfereGov" if k == "Nº SICONV/SIAFI" else k): v for k, v in siggo_params.items()}
    dfs["siggo"] = (siggo_tab, siggo_params)

    bruto = g.carregar("df_siggo_transferencia.csv", dtype=str).fillna("")
    for c in bruto.columns:
        bruto[c] = bruto[c].str.strip()
    siggo = bruto.drop_duplicates("NUTRANSFERENCIA").set_index("NUTRANSFERENCIA")

    api_linhas, reverso = construir(dfs, siggo)
    api_df, api_params = tabela_api(api_linhas)
    ug_df, ug_params = tabela_ug(api_df)
    cz_siggo_df, cz_siggo_params = tabela_cz_siggo(siggo_tab, reverso)

    try:
        sem_especiais = len(pd.read_csv(RAIZ / "df_especiais_plano_acao.csv")) == 0
    except Exception:  # noqa: BLE001
        sem_especiais = True

    tabelas = {"ug": (ug_df, ug_params, None), "cz_api": (api_df, api_params, COLS_API_TELA), "cz_siggo": (cz_siggo_df, cz_siggo_params, None),
               "parcerias": dfs["parcerias"] + (None,), "especiais": dfs["especiais"] + (None,), "fundoafundo": dfs["fundoafundo"] + (None,),
               "siconv": dfs["siconv"] + (None,), "siggo": dfs["siggo"] + (None,)}
    nav, conteudo = [], []
    for aba in ABAS:
        aid = aba["id"]
        ativo = "ativo" if aid == "resumo" else ""
        badge = "" if aid == "resumo" else f' <span class="badge-count">{len(tabelas[aid][0])}</span>'
        nav.append(f'<button class="aba-btn {ativo}" data-aba="{aid}" onclick="mostrarAba(\'{aid}\')">{aba["icone"]} {aba["titulo"]}{badge}</button>')
        if aid == "resumo":
            corpo = resumo_html(api_df, ug_df, ug_params)
        else:
            df, params, exibir = tabelas[aid]
            filtros = {"ug": lambda: filtros_ug(len(df)), "cz_api": lambda: filtros_api(df), "cz_siggo": lambda: filtros_cz_siggo(df),
                       "siggo": lambda: filtros_siggo(df)}.get(aid, lambda: g.montar_filtros(df, aid))()
            corpo = f'<p class="fonte-info">Fonte: {FONTES_DESCR[aid]}</p>{filtros}{tabela_html(df, aid, params, exibir)}'
        conteudo.append(f'<section class="aba-conteudo {ativo}" id="conteudo-{aid}">{corpo}</section>')

    html = (TEMPLATE.replace("@@CSS_BASE@@", css_base()).replace("@@CSS_EXTRA@@", CSS_EXTRA)
            .replace("@@DATA@@", pd.Timestamp.now().strftime("%d/%m/%Y %H:%M"))
            .replace("@@AVISO@@", aviso_html(len(bruto), len(siggo_tab), sem_especiais))
            .replace("@@NAV@@", "".join(nav)).replace("@@CONTEUDO@@", "".join(conteudo)).replace("@@JS@@", JS_COMUM))
    SAIDA.write_text(html, encoding="utf-8")
    inst = agrupar_instrumentos(api_df)
    print(f"Painel salvo em {SAIDA} ({SAIDA.stat().st_size / 1e6:.1f} MB)")
    print(f"  ótica API: {len(api_df)} linhas ({len(inst)} instrumentos) | por UG: {len(ug_df)} UGs | ótica SIGGo: {len(cz_siggo_df)} registros")
    print(inst["_grupo"].value_counts().to_string())


if __name__ == "__main__":
    main()
