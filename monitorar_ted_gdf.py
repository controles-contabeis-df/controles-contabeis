"""
Monitoramento semanal da API de TED do Transferegov: avisa por e-mail quando um Termo de Execucao
Descentralizada (TED) passa a citar orgaos do GDF.

Contexto: o TED so ocorre entre orgaos e entidades da Uniao. O GDF nao e parte, mas o Decreto
federal nº 10.426/2020 (art. 16, §3º, III, e §4º) admite que a executora federal descentralize a
execucao por convenio, acordo ou ajuste com entes federativos, se o TED previr. Esses ajustes nao
passam pelo modulo de convenios do Transferegov; este script procura, nos textos dos TEDs, indicios
de que o GDF seja executor, parceiro ou beneficiario.

O que faz:
  1. Baixa os planos de acao do TED (todos) e compara com a execucao anterior (ted_monitor/estado.json).
  2. Para planos NOVOS ou ALTERADOS (situacao, valor, vigencia, objeto), baixa metas, etapas, analises e
     pareceres e procura termos do GDF (padroes em PADRAO_GDF).
  3. Envia e-mail so se houver achado novo (plano novo com mencao, mencao nova em plano ja conhecido, ou
     plano com mencao que passou a APROVADO). Na primeira execucao grava a linha de base sem enviar e-mail.
  4. Em caso de falha da API, registra e envia um aviso (no maximo um por semana).

Cadencia semanal: a rotina diaria dos paineis (atualizar_paineis.ps1) chama este script todo dia, mas ele
so executa de fato quando a ultima execucao tem 6 dias ou mais (ted_monitor/estado.json). Nos demais dias
termina em instantes, sem consultar a API.

Uso:
    python monitorar_ted_gdf.py               # execucao normal: respeita a cadencia semanal
    python monitorar_ted_gdf.py --agora       # executa agora, ignorando a cadencia
    python monitorar_ted_gdf.py --dry-run     # executa agora, sem e-mail e sem gravar o estado
    python monitorar_ted_gdf.py --linha-de-base   # refaz a linha de base sem e-mail
    python monitorar_ted_gdf.py --teste-email # envia um e-mail de teste

Grava somente em ted_monitor/ (fora do Git) e em logs/. Nao toca na rotina diaria dos paineis.
"""

import argparse
import base64
import hashlib
import html
import json
import re
import time
import unicodedata
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

PASTA = Path(__file__).resolve().parent
DADOS = PASTA / "ted_monitor"
ESTADO = DADOS / "estado.json"
BASE = "https://api-publica.transferegov.gestao.gov.br/ted"
TOKEN = PASTA / "gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
REMETENTE = "contabilidadegeraldodf@gmail.com"
DESTINATARIOS = ["clarissa.barbosa@economia.df.gov.br"]

# Orgaos e expressoes do GDF (texto sem acento e em maiusculas). Tribunal de Justica e Ministerio Publico do
# DF e Territorios e a Superintendencia de Agricultura no DF sao federais e nao entram.
PADRAO_GDF = re.compile(
    r"GOVERNO DO DISTRITO FEDERAL|\bGDF\b|\bGOVERNO DO DF\b"
    r"|SECRETARIA (?:DE|DO|DA) ESTADO[^.;]{0,60}(?:DISTRITO FEDERAL|\bDF\b)"
    r"|SECRETARIA[^.;]{0,40}\b(?:DO|DE) (?:DISTRITO FEDERAL|DF)\b"
    r"|\bSES-?DF\b|\bSEEC-?DF\b|\bSEDF\b|\bPMDF\b|\bCBMDF\b|\bPCDF\b|\bDETRAN-?DF\b|\bNOVACAP\b|\bCAESB\b"
    r"|\bFAP-?DF\b|\bEMATER-?DF\b|\bCODHAB\b|\bDER-?DF\b|\bTERRACAP\b"
    r"|CORPO DE BOMBEIROS MILITAR DO DISTRITO|POLICIA (?:MILITAR|CIVIL) DO DISTRITO"
    r"|ADMINISTRACAO REGIONAL|ADMINISTRACOES REGIONAIS|FUNDO CONSTITUCIONAL DO DISTRITO"
)
# Palavras que indicam instrumento ou papel de execucao junto ao GDF (prioridade alta)
PADRAO_PAPEL = re.compile(r"CONVENIO|AJUSTE|ACORDO|TERMO DE (?:COOPERACAO|FOMENTO|COLABORACAO)|COOPERACAO TECNICA|EXECUTOR|EXECUTAR|EXECUCAO DESCENTRALIZADA|CELEBRAR|CELEBRA|REPASSE|CONTRAPARTIDA")
SITUACOES_APROVADAS = {"APROVADO", "ANALISE_APROVADA"}
# Enumeracoes genericas ("secretarias dos estados e do Distrito Federal") nao identificam o GDF
GENERICAS = re.compile(r"(?:ESTADOS|UNIDADES DA FEDERACAO|ENTES FEDERATIVOS)(?:[^.;]{0,30}?),? (?:E )?(?:DO )?DISTRITO FEDERAL")


def sem_acento(t):
    return unicodedata.normalize("NFKD", str(t)).encode("ascii", "ignore").decode().upper()


def get(ep, **params):
    ultimo = None
    for t in range(6):
        try:
            r = requests.get(f"{BASE}/{ep}", params=params, timeout=90)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            ultimo = e
            time.sleep(3 * (t + 1))
    raise RuntimeError(f"falha ao consultar {ep} {params}: {ultimo}")


def todas(ep, **filtros):
    saida, pag = [], 1
    while True:
        j = get(ep, pagina=pag, tamanho_da_pagina=100, **filtros)
        saida += j["data"]
        if pag >= j["total_pages"]:
            return saida
        pag += 1


def assinatura_plano(p):
    chaves = ["tx_situacao_plano_acao", "vl_total_plano_acao", "dt_inicio_vigencia", "dt_fim_vigencia", "tx_objeto_plano_acao", "tx_justificativa_plano_acao"]
    return hashlib.sha1("|".join(str(p.get(k)) for k in chaves).encode("utf-8")).hexdigest()


def trechos(texto, origem):
    """Trechos do texto que citam o GDF, com a marca de papel (instrumento/execucao) quando houver."""
    achados, base = [], GENERICAS.sub(lambda mm: " " * len(mm.group(0)), sem_acento(texto))
    for m in PADRAO_GDF.finditer(base):
        i, f = max(0, m.start() - 160), m.end() + 200
        janela = base[i:f]
        achados.append({"origem": origem, "termo": m.group(0)[:60], "papel": bool(PADRAO_PAPEL.search(janela)),
                        "trecho": re.sub(r"\s+", " ", str(texto)[i:f]).strip()})
        if len(achados) >= 3:
            break
    return achados


def textos_do_plano(p):
    """Todos os textos do plano: objeto/justificativa, metas, etapas, analises e pareceres."""
    saida = [("objeto/justificativa", f"{p.get('tx_objeto_plano_acao') or ''} {p.get('tx_justificativa_plano_acao') or ''}")]
    pid = p["id_plano_acao"]
    for meta in todas("planos-acao-metas", id_plano_acao=pid):
        saida.append(("meta", f"{meta.get('tx_nome_meta') or ''} {meta.get('tx_descricao_meta') or ''}"))
        for et in todas("planos-acao-metas-etapas", id_meta=meta["id_meta"]):
            saida.append(("etapa", f"{et.get('tx_nome_etapa') or ''} {et.get('tx_descricao_etapa') or ''}"))
    for a in todas("planos-acao-analises", id_plano_acao=pid):
        saida.append(("análise", a.get("tx_justificativa_analise") or ""))
    for a in todas("planos-acao-pareceres", id_plano_acao=pid):
        saida.append(("parecer", a.get("tx_parecer") or ""))
    return saida


def baixar_lote():
    """Baixa de uma vez os textos de TODOS os planos (usado na linha de base e quando ha muitos planos novos)."""
    por_plano = {}
    metas = todas("planos-acao-metas")
    meta_plano = {m["id_meta"]: m["id_plano_acao"] for m in metas}
    for m in metas:
        por_plano.setdefault(m["id_plano_acao"], []).append(("meta", f"{m.get('tx_nome_meta') or ''} {m.get('tx_descricao_meta') or ''}"))
    for e in todas("planos-acao-metas-etapas"):
        pid = meta_plano.get(e["id_meta"])
        if pid is not None:
            por_plano.setdefault(pid, []).append(("etapa", f"{e.get('tx_nome_etapa') or ''} {e.get('tx_descricao_etapa') or ''}"))
    for x in todas("planos-acao-analises"):
        por_plano.setdefault(x["id_plano_acao"], []).append(("análise", x.get("tx_justificativa_analise") or ""))
    for x in todas("planos-acao-pareceres"):
        por_plano.setdefault(x["id_plano_acao"], []).append(("parecer", x.get("tx_parecer") or ""))
    return por_plano


def analisar_plano(p, lote=None):
    achados = []
    if lote is not None:
        textos = [("objeto/justificativa", f"{p.get('tx_objeto_plano_acao') or ''} {p.get('tx_justificativa_plano_acao') or ''}")] + lote.get(p["id_plano_acao"], [])
    else:
        textos = textos_do_plano(p)
    for origem, txt in textos:
        if txt.strip():
            achados += trechos(txt, origem)
    # remove repeticoes do mesmo trecho
    vistos, unicos = set(), []
    for a in achados:
        k = a["trecho"][:120]
        if k not in vistos:
            vistos.add(k)
            unicos.append(a)
    return unicos


def ler_estado():
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    return None


def gravar_estado(est):
    DADOS.mkdir(exist_ok=True)
    ESTADO.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")


def monta_email(novos):
    def linha(p, a):
        flag = "<b style='color:#b00020'>possível instrumento/execução</b>" if any(x["papel"] for x in a["achados"]) else "menção"
        itens = "".join(f"<li><i>{html.escape(x['origem'])}</i> — {html.escape(x['trecho'][:340])}</li>" for x in a["achados"][:3])
        return (f"<tr><td>{html.escape(p['codigo_plano_acao'])}</td><td>{html.escape(str(p.get('unidade_responsavel_execucao') or ''))}</td>"
                f"<td>{html.escape(str(p.get('tx_situacao_plano_acao') or ''))}</td><td style='text-align:right'>R$ {float(p.get('vl_total_plano_acao') or 0):,.2f}</td>"
                f"<td>{html.escape(str(p.get('dt_inicio_vigencia') or ''))} a {html.escape(str(p.get('dt_fim_vigencia') or ''))}</td>"
                f"<td>{flag}<ul>{itens}</ul></td></tr>")
    ordem = sorted(novos, key=lambda x: (not any(a["papel"] for a in x["achados"]), x["plano"]["tx_situacao_plano_acao"] not in SITUACOES_APROVADAS))
    corpo = (
        f"<p>Monitoramento semanal da API de TED — {datetime.now():%d/%m/%Y %H:%M}.</p>"
        f"<p>Foram encontrados <b>{len(novos)}</b> plano(s) de ação de TED com menção a órgãos do GDF que ainda não tinham sido avisados:</p>"
        "<table border='1' cellpadding='5' cellspacing='0' style='border-collapse:collapse;font-size:12px'>"
        "<tr style='background:#0d1b3e;color:#fff'><th>Plano de ação</th><th>Executora federal</th><th>Situação</th><th>Valor</th><th>Vigência</th><th>Trechos</th></tr>"
        + "".join(linha(x["plano"], x) for x in ordem) + "</table>"
        "<p style='font-size:12px;color:#555'>O TED ocorre entre órgãos da União; a menção ao GDF não indica, por si só, repasse ou instrumento com o Distrito Federal. "
        "O Decreto nº 10.426/2020 (art. 16, §3º, III, e §4º) permite que a executora federal descentralize a execução por convênio, acordo ou ajuste com entes "
        "federativos; se for o caso, o ajuste pode não constar do módulo de convênios do Transferegov. Verificar o termo no processo SEI do TED e, se houver ajuste com o "
        "GDF, orientar a UG a cadastrá-lo no SIGGo (IN nº 13/2026).</p>")
    return corpo


def enviar(assunto, corpo):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    servico = build("gmail", "v1", credentials=creds)
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = assunto, REMETENTE, ", ".join(DESTINATARIOS)
    msg.attach(MIMEText(corpo, "html", "utf-8"))
    servico.users().messages().send(userId="me", body={"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}).execute()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--linha-de-base", action="store_true")
    ap.add_argument("--teste-email", action="store_true")
    ap.add_argument("--agora", action="store_true")
    a = ap.parse_args()
    if a.teste_email:
        enviar("[TESTE] Monitoramento de TEDs com menção ao GDF", "<p>Teste do aviso semanal. Se você recebeu esta mensagem, o envio está funcionando.</p>")
        print("E-mail de teste enviado.")
        return
    est = ler_estado()
    if est and not (a.agora or a.dry_run or a.linha_de_base):
        dias = (datetime.now() - datetime.strptime(est["ultima_execucao"], "%Y-%m-%d %H:%M")).total_seconds() / 86400
        if dias < 6:
            print(f"Monitoramento semanal: última execução há {dias:.1f} dia(s); próxima a partir de {6 - dias:.1f} dia(s). Nada a fazer hoje.")
            return
    try:
        data_api = get("data-atualizacao").get("data_ultima_atualizacao")
        planos = todas("planos-acao")
        baseline = est is None or a.linha_de_base
        anterior = {} if baseline else est["planos"]
        a_analisar = [p for p in planos if str(p["id_plano_acao"]) not in anterior or anterior[str(p["id_plano_acao"])]["assinatura"] != assinatura_plano(p)]
        print(f"API atualizada em {data_api} | planos: {len(planos)} | a analisar (novos/alterados): {len(a_analisar)}" + (" | LINHA DE BASE" if baseline else ""))
        novo_estado = {} if baseline else dict(anterior)
        alertas = []
        lote = baixar_lote() if len(a_analisar) > 150 else None
        for i, p in enumerate(a_analisar, 1):
            achados = analisar_plano(p, lote)
            pid = str(p["id_plano_acao"])
            ant = anterior.get(pid, {})
            hashes = sorted(hashlib.sha1(x["trecho"][:120].encode("utf-8")).hexdigest() for x in achados)
            novo_estado[pid] = {"assinatura": assinatura_plano(p), "mencoes": hashes, "situacao": p["tx_situacao_plano_acao"], "codigo": p["codigo_plano_acao"]}
            ja = set(ant.get("mencoes", []))
            nova_mencao = bool(set(hashes) - ja)
            virou_aprovado = bool(hashes) and p["tx_situacao_plano_acao"] in SITUACOES_APROVADAS and ant.get("situacao") not in SITUACOES_APROVADAS and ant
            if achados and (nova_mencao or virou_aprovado):
                alertas.append({"plano": p, "achados": achados})
            if i % 100 == 0:
                print(f"  analisados {i}/{len(a_analisar)}", flush=True)
        for p in planos:   # planos nao alterados mantem o estado; os novos sem mencao entram com lista vazia
            pid = str(p["id_plano_acao"])
            if pid not in novo_estado:
                novo_estado[pid] = {"assinatura": assinatura_plano(p), "mencoes": [], "situacao": p["tx_situacao_plano_acao"], "codigo": p["codigo_plano_acao"]}
        resultado = {"planos": novo_estado, "ultima_execucao": f"{datetime.now():%Y-%m-%d %H:%M}", "data_atualizacao_api": data_api, "ultimo_aviso_falha": (est or {}).get("ultimo_aviso_falha")}
    except Exception as e:  # noqa: BLE001
        print("FALHA:", e)
        semana = f"{datetime.now():%G-W%V}"
        if not a.dry_run and (est or {}).get("ultimo_aviso_falha") != semana:
            enviar("Monitoramento de TEDs: falha ao consultar a API", f"<p>O monitoramento semanal de TEDs não conseguiu consultar a API em {datetime.now():%d/%m/%Y %H:%M}.</p><pre>{html.escape(str(e))[:800]}</pre>")
            gravar_estado({**(est or {"planos": {}}), "ultimo_aviso_falha": semana})
        raise SystemExit(1)

    comMencao = sum(1 for v in novo_estado.values() if v["mencoes"])
    print(f"planos com menção ao GDF (acumulado): {comMencao} | novos alertas: {len(alertas)}")
    if baseline:
        print("Linha de base gravada; nenhum e-mail enviado.")
    elif alertas:
        corpo = monta_email(alertas)
        assunto = f"TED com menção ao GDF: {len(alertas)} novo(s) plano(s) — {datetime.now():%d/%m/%Y}"
        if a.dry_run:
            print("[DRY-RUN] assunto:", assunto)
            for x in alertas:
                print(" -", x["plano"]["codigo_plano_acao"], x["plano"]["unidade_responsavel_execucao"], x["plano"]["tx_situacao_plano_acao"], "|", x["achados"][0]["trecho"][:150])
        else:
            enviar(assunto, corpo)
            print("E-mail enviado para:", ", ".join(DESTINATARIOS))
    else:
        print("Nada novo; nenhum e-mail enviado.")
    if not a.dry_run:
        gravar_estado(resultado)


if __name__ == "__main__":
    main()
