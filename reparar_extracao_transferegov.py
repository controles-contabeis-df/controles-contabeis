"""
Repara lacunas deixadas por falhas transitorias (HTTP 502/timeout) da API
publica do TransfereGov na extracao: refaz, COM novas tentativas, so os itens
que ficaram faltando em df_parcerias_* e df_fundoafundo_subtransacoes.csv.
Rodar depois de extrair_transferegov_df.py.
"""
import time

import pandas as pd
import requests

from extrair_transferegov_df import PASTA_SAIDA, salvar

BASE = "https://api-publica.transferegov.gestao.gov.br"


def buscar(modulo, endpoint, campo, valor, tentativas=6):
    for t in range(tentativas):
        try:
            r = requests.get(f"{BASE}/{modulo}/{endpoint}", params={campo: valor, "pagina": 1, "tamanho_da_pagina": 100}, timeout=60)
            r.raise_for_status()
            return r.json().get("data", []), True
        except requests.exceptions.RequestException:
            time.sleep(3 * (t + 1))
    return [], False


def ler(nome):
    return pd.read_csv(PASTA_SAIDA / nome, dtype=str, low_memory=False)


def main():
    falhas = 0
    # ---- Gestao de Parcerias: propostas sem parceria + filhos de parcerias com falha
    prop = ler("df_parcerias_proposta.csv")
    parc = ler("df_parcerias_parceria.csv")
    novas_parc, emp, doc, ordem, conta = [], [], [], [], []
    faltam = sorted(set(prop["id_proposta"]) - set(parc["id_proposta"]))
    print(f"propostas sem parceria: {len(faltam)}")
    for idp in faltam:
        dados, ok = buscar("parcerias", "parceria", "id_proposta", idp)
        falhas += (not ok)
        novas_parc.extend(dados)
    todas = pd.concat([parc, pd.DataFrame(novas_parc).astype(str)], ignore_index=True) if novas_parc else parc
    todas = todas.drop_duplicates("id_parceria")

    # refaz os filhos de TODAS as parcerias novas e das que nao tem nenhum filho
    emp_old, doc_old = ler("df_parcerias_empenho.csv"), ler("df_parcerias_documento_habil.csv")
    ord_old, conta_old = ler("df_parcerias_ordem_pagamento.csv"), ler("df_parcerias_conta.csv")
    ids_novos = {str(p["id_parceria"]) for p in novas_parc}
    sem_conta = set(todas["id_parceria"]) - set(conta_old["id_parceria"])
    refazer = ids_novos | sem_conta
    print(f"parcerias com filhos a refazer: {len(refazer)}")
    for idpar in sorted(refazer):
        e, ok1 = buscar("parcerias", "empenho-parceria", "id_parceria", idpar)
        d, ok2 = buscar("parcerias", "documento-habil", "id_parceria", idpar)
        c, ok3 = buscar("parcerias", "parceria-conta", "id_parceria", idpar)
        falhas += (not ok1) + (not ok2) + (not ok3)
        emp.extend(e); doc.extend(d); conta.extend(c)
        for x in d:
            o, ok4 = buscar("parcerias", "ordem-pagamento", "id_documento_habil", x.get("id_documento_habil"))
            falhas += (not ok4); ordem.extend(o)
    # ordens de documentos hábeis existentes sem nenhuma ordem
    docs_sem_ordem = set(doc_old["id_documento_habil"]) - set(ord_old["id_documento_habil"])
    for idd in sorted(docs_sem_ordem):
        o, ok = buscar("parcerias", "ordem-pagamento", "id_documento_habil", idd)
        falhas += (not ok); ordem.extend(o)

    def junta(velho, novo, chave):
        if not novo:
            return velho
        return pd.concat([velho, pd.DataFrame(novo).astype(str)], ignore_index=True).drop_duplicates(chave, keep="last")

    salvar(todas, "df_parcerias_parceria.csv")
    salvar(junta(emp_old, emp, "id_empenho_parceria" if "id_empenho_parceria" in emp_old.columns else list(emp_old.columns)[0]), "df_parcerias_empenho.csv")
    salvar(junta(doc_old, doc, "id_documento_habil"), "df_parcerias_documento_habil.csv")
    salvar(junta(ord_old, ordem, list(ord_old.columns)[0]), "df_parcerias_ordem_pagamento.csv")
    salvar(junta(conta_old, conta, "id_parceria_conta"), "df_parcerias_conta.csv")

    # ---- Fundo a Fundo: lancamentos sem nenhuma subtransacao registrada
    lanc = ler("df_fundoafundo_lancamentos.csv")
    sub = ler("df_fundoafundo_subtransacoes.csv")
    sem_sub = sorted(set(lanc["id_lancamento_gestao_financeira"]) - set(sub["id_lancamento_gestao_financeira"]))
    print(f"lancamentos sem subtransacao a reconsultar: {len(sem_sub)}")
    novas = []
    for idl in sem_sub:
        s, ok = buscar("fundoafundo", "gestao-financeira-subtransacoes", "id_lancamento_gestao_financeira", idl)
        falhas += (not ok); novas.extend(s)
    if novas:
        chave = "id_subtransacao_gestao_financeira" if "id_subtransacao_gestao_financeira" in sub.columns else list(sub.columns)[0]
        salvar(pd.concat([sub, pd.DataFrame(novas).astype(str)], ignore_index=True).drop_duplicates(chave), "df_fundoafundo_subtransacoes.csv")
    print(f"recuperadas: {len(novas_parc)} parcerias, {len(novas)} subtransacoes | consultas que ainda falharam: {falhas}")


if __name__ == "__main__":
    main()
