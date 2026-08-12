"""
Verificação dos lançamentos intragovernamentais — GDF consolidado (MCASP, subtítulo 2)

Verifica a equação patrimonial intra sobre VLANCAMENTOCONTABIL (INMES 0–mes, MIL2026):

  Equação A — Classes 1+3 (natureza devedora) = Classes 2+4 (natureza credora)
    Divergência = saldo devedor intra − saldo credor intra

Quando há divergência, detalha os documentos e UGs emitentes responsáveis.
Envia e-mail de alerta quando |divergência global| > R$ 0,01.

Uso:
    python extrair_equacao_balanco.py
    python extrair_equacao_balanco.py --mes 7 --ano 2026
    python extrair_equacao_balanco.py --dry-run
"""
import argparse, base64, os
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import oracledb
from dotenv import load_dotenv

PASTA = Path(__file__).parent
load_dotenv(PASTA / ".env")

ORACLE_USER = os.environ["ORACLE_USER"]
ORACLE_PASS = os.environ["ORACLE_PASS"]
ORACLE_DSN  = os.environ["ORACLE_DSN"]

INSTANT_CLIENT_DIR = r"C:\oracle\instantclient_23_0"
TOKEN  = PASTA / "gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

REMETENTE     = "contabilidadegeraldodf@gmail.com"
DESTINATARIOS = [
    "clarissa.barbosa@economia.df.gov.br",
    "daniel.mello@economia.df.gov.br",
]

MESES = {1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
         7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}

MAX_DOCS_EMAIL = 50  # exibe até este número de documentos na tabela do e-mail

# ─── Consulta global: saldo devedor vs credor (subtítulo 2) ──────────────────
SQL_GLOBAL = """
SELECT
  SUM(CASE WHEN SUBSTR(TO_CHAR(COCONTACONTABIL),1,1) IN ('1','3')
           THEN CASE WHEN INDEBITOCREDITO='D' THEN  VALANCAMENTO
                     WHEN INDEBITOCREDITO='C' THEN -VALANCAMENTO
                     ELSE 0 END
           ELSE 0 END) AS SALDO_DEV_INTRA,
  SUM(CASE WHEN SUBSTR(TO_CHAR(COCONTACONTABIL),1,1) IN ('2','4')
           THEN CASE WHEN INDEBITOCREDITO='C' THEN  VALANCAMENTO
                     WHEN INDEBITOCREDITO='D' THEN -VALANCAMENTO
                     ELSE 0 END
           ELSE 0 END) AS SALDO_CRED_INTRA
FROM MIL2026.VLANCAMENTOCONTABIL
WHERE INMES <= :mes
  AND SUBSTR(TO_CHAR(COCONTACONTABIL),1,1) IN ('1','2','3','4')
  AND SUBSTR(TO_CHAR(COCONTACONTABIL),5,1) = '2'
"""

# ─── Consulta detalhada: divergência por documento emitente ──────────────────
# Agrupa por (NUDOCUMENTO, COUG_EMIT, COGESTAO_EMIT) — sem INMES — para
# capturar todas as pernas do documento mesmo que em meses diferentes.
SQL_DOCS = """
WITH doc_div AS (
  SELECT
    v.COGESTAO,
    v.COUG,
    TRIM(v.NUDOCUMENTO)  AS NUDOCUMENTO,
    ROUND(SUM(CASE WHEN v.INDEBITOCREDITO='D' THEN  v.VALANCAMENTO
                   WHEN v.INDEBITOCREDITO='C' THEN -v.VALANCAMENTO
                   ELSE 0 END), 2) AS DIV_A,
    TO_CHAR(MIN(v.DALANCAMENTO), 'DD/MM/YYYY') AS DATA
  FROM MIL2026.VLANCAMENTOCONTABIL v
  WHERE v.INMES <= :mes
    AND SUBSTR(TO_CHAR(v.COCONTACONTABIL),1,1) IN ('1','2','3','4')
    AND SUBSTR(TO_CHAR(v.COCONTACONTABIL),5,1) = '2'
  GROUP BY v.COGESTAO, v.COUG, TRIM(v.NUDOCUMENTO)
  HAVING ABS(ROUND(SUM(CASE WHEN v.INDEBITOCREDITO='D' THEN  v.VALANCAMENTO
                            WHEN v.INDEBITOCREDITO='C' THEN -v.VALANCAMENTO
                            ELSE 0 END), 2)) > 0.005
)
SELECT
  d.COGESTAO,
  d.COUG,
  ge.NOGESTAO,
  ue.NOUG,
  d.NUDOCUMENTO,
  d.DATA,
  d.DIV_A
FROM doc_div d
LEFT JOIN MIL2026.VUNIDADEGESTORA ue ON ue.COUG      = d.COUG
LEFT JOIN MIL2026.GESTAO          ge ON ge.COGESTAO  = d.COGESTAO
ORDER BY ABS(d.DIV_A) DESC, d.COUG, d.NUDOCUMENTO
"""


# ─── Formatação ───────────────────────────────────────────────────────────────
def brl(v):
    if v is None:
        return "—"
    v = round(v, 2)
    if v == 0:
        return "R$ 0,00"
    sinal = "-" if v < 0 else ""
    return f"{sinal}R$ {abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def fmt_gestao(v):
    try:
        return str(int(v)).zfill(6)
    except Exception:
        return str(v)

def cor_valor(v):
    if v is None or round(v, 2) == 0:
        return "#6b7a99"
    return "#c0392b" if v < 0 else "#1a7a44"


# ─── Filtragem: mantém apenas documentos que efetivamente abrem a equação ────
# Documentos estornados (contrabalanceados por outros de valor oposto) são
# removidos em duas passagens: pares exatos (v, -v) e grupos de três.
def filtrar_contribuintes(docs, tolerancia=0.005):
    from collections import defaultdict

    itens = [(round(float(d["DIV_A"]), 2), i) for i, d in enumerate(docs)]
    cancelados = set()

    # Passagem 1 — pares exatos (v e -v)
    pos_map = defaultdict(list)
    neg_map = defaultdict(list)
    for v, i in itens:
        (pos_map if v > 0 else neg_map)[v].append(i)

    for v, pos_idxs in pos_map.items():
        neg_idxs = neg_map.get(-v, [])
        for k in range(min(len(pos_idxs), len(neg_idxs))):
            cancelados.add(pos_idxs[k])
            cancelados.add(neg_idxs[k])

    # Passagem 2 — trios que somam zero entre os remanescentes
    rem = [(v, i) for v, i in itens if i not in cancelados]
    n = len(rem)
    for a in range(n):
        va, ia = rem[a]
        if ia in cancelados:
            continue
        for b in range(a + 1, n):
            vb, ib = rem[b]
            if ib in cancelados:
                continue
            for c in range(b + 1, n):
                vc, ic = rem[c]
                if ic in cancelados:
                    continue
                if abs(va + vb + vc) < tolerancia:
                    cancelados.update([ia, ib, ic])
                    break

    return [docs[i] for _, i in itens if i not in cancelados]


# ─── HTML do e-mail ───────────────────────────────────────────────────────────
def montar_email_html(mes, ano, dev, cred, docs_contrib, total_encontrados):
    data_hora  = datetime.now().strftime("%d/%m/%Y às %H:%M")
    mes_label  = f"{MESES[mes]}/{ano}"
    div_global = round(dev - cred, 2)
    n_contrib  = len(docs_contrib)

    github_user = os.environ.get("GITHUB_USER", "")
    github_repo = os.environ.get("GITHUB_REPO", "")
    panel_url   = f"https://{github_user}.github.io/{github_repo}/intra_lancamento.html"

    # ── Bloco resumo global ──
    resumo = f"""
<table border="1" cellpadding="8" cellspacing="0"
       style="border-collapse:collapse;font-size:12px;width:100%;margin-bottom:20px">
  <thead style="background:#0d1b3e;color:#fff">
    <tr>
      <th style="text-align:left">Equação</th>
      <th style="text-align:right">Saldo devedor intra (1+3)</th>
      <th style="text-align:right">Saldo credor intra (2+4)</th>
      <th style="text-align:right">Divergência</th>
    </tr>
  </thead>
  <tbody>
    <tr style="background:#fff8f8">
      <td><b>Classes 1+3 = Classes 2+4 (subtítulo 2)</b></td>
      <td style="text-align:right;font-family:monospace">{brl(dev)}</td>
      <td style="text-align:right;font-family:monospace">{brl(cred)}</td>
      <td style="text-align:right;font-family:monospace;color:{cor_valor(div_global)}">
        <b>{brl(div_global)}</b>
      </td>
    </tr>
  </tbody>
</table>"""

    # ── Bloco detalhamento por documento ──
    exibir    = docs_contrib[:MAX_DOCS_EMAIL]
    restantes = n_contrib - len(exibir)

    linhas_docs = ""
    for i, d in enumerate(exibir):
        gestao     = fmt_gestao(d["COGESTAO"])
        ug         = str(d["COUG"])
        noug       = (d["NOUG"]    or "").strip()
        nogest     = (d["NOGESTAO"] or "").strip()
        nudoc      = str(d["NUDOCUMENTO"] or "")
        data_lanc  = str(d["DATA"] or "—")
        div_a      = d["DIV_A"]
        bg         = "#f9f9f9" if i % 2 else "#ffffff"
        emit_label = f"{gestao}-{ug}"
        emit_nome  = f"{nogest} · {noug}" if nogest else noug
        linhas_docs += f"""
    <tr style="background:{bg}">
      <td style="font-family:monospace;white-space:nowrap">{emit_label}</td>
      <td style="font-size:11px;color:#6b7a99">{emit_nome}</td>
      <td style="font-family:monospace;white-space:nowrap">{nudoc}</td>
      <td style="white-space:nowrap;color:#6b7a99">{data_lanc}</td>
      <td style="text-align:right;font-family:monospace;color:{cor_valor(div_a)}">
        <b>{brl(div_a)}</b>
      </td>
    </tr>"""

    nota_restantes = ""
    if restantes > 0:
        nota_restantes = (f'<p style="font-size:11px;color:#6b7a99;margin-top:4px">'
                          f'Exibindo {len(exibir)} de {n_contrib} documentos. '
                          f'<a href="{panel_url}">Ver lista completa no painel.</a></p>')

    nota_estornados = ""
    estornados = total_encontrados - n_contrib
    if estornados > 0:
        nota_estornados = (f'<p style="font-size:11px;color:#6b7a99;margin-top:6px">'
                           f'{estornados} documento(s) adicional(is) com divergência individual '
                           f'foram identificados, porém seus valores se anulam mutuamente '
                           f'(estornos) e não contribuem para a divergência global.</p>')

    detalhe = f"""
<p style="font-size:13px;margin-bottom:6px">
  <b>{n_contrib} documento(s)</b> com divergência:
</p>
<table border="1" cellpadding="7" cellspacing="0"
       style="border-collapse:collapse;font-size:12px;width:100%">
  <thead style="background:#162550;color:#c8d8ec">
    <tr>
      <th style="text-align:left;white-space:nowrap">Gestão-UG Emitente</th>
      <th style="text-align:left">Nome</th>
      <th style="text-align:left;white-space:nowrap">Nº Documento</th>
      <th style="text-align:left;white-space:nowrap">Data Lanç.</th>
      <th style="text-align:right;white-space:nowrap">Divergência Eq. A</th>
    </tr>
  </thead>
  <tbody>{linhas_docs}
  </tbody>
  <tfoot>
    <tr style="background:#e8f0f8">
      <td colspan="4"><b>Total</b></td>
      <td style="text-align:right;font-family:monospace;color:{cor_valor(div_global)}">
        <b>{brl(div_global)}</b>
      </td>
    </tr>
  </tfoot>
</table>
{nota_restantes}
{nota_estornados}"""

    return f"""<html><body style="font-family:Arial,sans-serif;font-size:13px;color:#1a2033;max-width:960px">
<p>Prezados,</p>
<p>O monitoramento automático dos lançamentos intragovernamentais identificou
a(s) divergência(s) a seguir que requer(em) atenção.</p>

<h3 style="font-size:13px;margin-bottom:6px;color:#0d1b3e">Resumo global</h3>
{resumo}

<h3 style="font-size:13px;margin-bottom:6px;color:#0d1b3e">Detalhamento por documento</h3>
{detalhe}

<p style="font-size:12px;color:#1a2033;margin-top:20px">
  Para análise completa com filtros por UG, conta contábil e evento, acesse o painel:<br>
  <a href="{panel_url}">{panel_url}</a>
</p>

<br>
<p style="font-size:11px;color:#6b7a99">
  Esta mensagem é gerada automaticamente. Verificação realizada em {data_hora}.
</p>
</body></html>"""


# ─── Envio ou pré-visualização ────────────────────────────────────────────────
def enviar_ou_preview(html, mes, ano, dry_run):
    assunto = (f"ALERTA CONTÁBIL — Lançamentos Intragovernamentais — "
               f"{MESES[mes]}/{ano} — {datetime.now().strftime('%d/%m/%Y')}")
    if dry_run:
        preview = PASTA / "equacao_balanco_email_preview.html"
        preview.write_text(html, encoding="utf-8")
        print(f"  [DRY-RUN] E-mail salvo para revisão: {preview}")
        print(f"  [DRY-RUN] Assunto seria: {assunto}")
        print(f"  [DRY-RUN] Destinatários: {', '.join(DESTINATARIOS)}")
        return

    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds   = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    service = build("gmail", "v1", credentials=creds)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"]    = REMETENTE
    msg["To"]      = ", ".join(DESTINATARIOS)
    msg.attach(MIMEText(html, "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"  Alerta enviado para: {', '.join(DESTINATARIOS)}")


# ─── Principal ────────────────────────────────────────────────────────────────
def main():
    hoje    = date.today()
    mes_def = hoje.month
    ano_def = hoje.year

    p = argparse.ArgumentParser()
    p.add_argument("--mes",     type=int, default=mes_def)
    p.add_argument("--ano",     type=int, default=ano_def)
    p.add_argument("--dry-run", action="store_true",
                   help="Não envia e-mail; salva pré-visualização em arquivo")
    a = p.parse_args()

    print(f"[{datetime.now():%H:%M:%S}] Verificando lançamentos intragovernamentais "
          f"({MESES[a.mes]}/{a.ano})…")

    oracledb.init_oracle_client(lib_dir=INSTANT_CLIENT_DIR)

    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS,
                          dsn=ORACLE_DSN) as conn:
        cur = conn.cursor()

        # 1. Verificação global
        print(f"  Classes 1+3 vs 2+4, subtítulo 2 (INMES 0–{a.mes})…")
        cur.execute(SQL_GLOBAL, mes=a.mes)
        r1   = cur.fetchone()
        dev  = float(r1[0] or 0)
        cred = float(r1[1] or 0)
        div1 = round(dev - cred, 2)

        print(f"  Saldo devedor intra (1+3): {brl(dev)}")
        print(f"  Saldo credor  intra (2+4): {brl(cred)}")
        print(f"  Divergência:               {brl(div1)}")

        if abs(div1) <= 0.01:
            print("  [OK] Nenhuma divergência detectada. Nenhum e-mail enviado.")
            return

        # 2. Detalhamento por documento
        print(f"  Buscando documentos com divergência individual…")
        cur.execute(SQL_DOCS, mes=a.mes)
        cols = [c[0] for c in cur.description]
        docs = [dict(zip(cols, row)) for row in cur.fetchall()]
        print(f"  {len(docs)} documento(s) encontrado(s) com divergência individual.")

        # Filtra apenas os que efetivamente abrem a equação (remove estornos)
        contrib = filtrar_contribuintes(docs)
        estornados = len(docs) - len(contrib)
        print(f"  {len(contrib)} contribuinte(s) líquido(s) "
              f"({estornados} estornado(s)/anulado(s) removido(s)).")
        for d in contrib:
            print(f"    {fmt_gestao(d['COGESTAO'])}-{d['COUG']}  "
                  f"{d['NUDOCUMENTO']}  {brl(d['DIV_A'])}")

        print("  [!] Gerando alerta…")

    html = montar_email_html(a.mes, a.ano, dev, cred, contrib, len(docs))
    enviar_ou_preview(html, a.mes, a.ano, a.dry_run)
    print("  Concluído.")


if __name__ == "__main__":
    main()
