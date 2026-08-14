"""
Monitoramento de Transferências Intragovernamentais — 351120XXX × 451120XXX

Verifica a equação:
  SUM(351120XXX) = SUM(451120XXX)

sobre lançamentos datados a partir de INICIO_MONITORAMENTO (inclusive).

Envia alerta por e-mail quando |divergência global| > R$ 0,01.
Quando a equação estiver equilibrada, nenhum e-mail é enviado.

Uso:
    python alerta_transferencias_intra.py
    python alerta_transferencias_intra.py --ano 2026
    python alerta_transferencias_intra.py --dry-run
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

# Data a partir da qual o monitoramento é válido (lançamentos anteriores são ignorados)
INICIO_MONITORAMENTO = date(2026, 8, 14)

MESES = {1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
         7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}

MAX_DOCS_EMAIL = 50


# ─── Consulta global: saldo 351120XXX vs 451120XXX no período ────────────────
SQL_GLOBAL = """
SELECT
  ROUND(SUM(CASE WHEN SUBSTR(TO_CHAR(COCONTACONTABIL),1,6) = '351120'
                 THEN CASE WHEN INDEBITOCREDITO='D' THEN  VALANCAMENTO
                           WHEN INDEBITOCREDITO='C' THEN -VALANCAMENTO
                           ELSE 0 END
                 ELSE 0 END), 2) AS SALDO_351,
  ROUND(SUM(CASE WHEN SUBSTR(TO_CHAR(COCONTACONTABIL),1,6) = '451120'
                 THEN CASE WHEN INDEBITOCREDITO='C' THEN  VALANCAMENTO
                           WHEN INDEBITOCREDITO='D' THEN -VALANCAMENTO
                           ELSE 0 END
                 ELSE 0 END), 2) AS SALDO_451
FROM MIL2026.VLANCAMENTOCONTABIL
WHERE DALANCAMENTO >= :inicio
  AND SUBSTR(TO_CHAR(COCONTACONTABIL),1,6) IN ('351120','451120')
"""

# ─── Consulta detalhada: divergência por documento ───────────────────────────
SQL_DOCS = """
WITH doc_div AS (
  SELECT
    v.COGESTAO,
    v.COUG,
    TRIM(v.NUDOCUMENTO) AS NUDOCUMENTO,
    ROUND(SUM(CASE WHEN INDEBITOCREDITO='D' THEN  VALANCAMENTO
                   WHEN INDEBITOCREDITO='C' THEN -VALANCAMENTO
                   ELSE 0 END), 2) AS DIV,
    TO_CHAR(MIN(v.DALANCAMENTO), 'DD/MM/YYYY') AS DATA
  FROM MIL2026.VLANCAMENTOCONTABIL v
  WHERE v.DALANCAMENTO >= :inicio
    AND SUBSTR(TO_CHAR(v.COCONTACONTABIL),1,6) IN ('351120','451120')
  GROUP BY v.COGESTAO, v.COUG, TRIM(v.NUDOCUMENTO)
  HAVING ABS(ROUND(SUM(CASE WHEN INDEBITOCREDITO='D' THEN  VALANCAMENTO
                             WHEN INDEBITOCREDITO='C' THEN -VALANCAMENTO
                             ELSE 0 END), 2)) > 0.005
)
SELECT
  d.COGESTAO,
  d.COUG,
  ge.NOGESTAO,
  ue.NOUG,
  d.NUDOCUMENTO,
  d.DATA,
  d.DIV
FROM doc_div d
LEFT JOIN MIL2026.VUNIDADEGESTORA ue ON ue.COUG     = d.COUG
LEFT JOIN MIL2026.GESTAO          ge ON ge.COGESTAO = d.COGESTAO
ORDER BY ABS(d.DIV) DESC, d.COUG, d.NUDOCUMENTO
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


# ─── HTML do e-mail ───────────────────────────────────────────────────────────
def montar_email_html(s351, s451, docs, data_hora, inicio):
    div_global  = round(s351 - s451, 2)
    n_docs      = len(docs)
    exibir      = docs[:MAX_DOCS_EMAIL]
    restantes   = n_docs - len(exibir)
    periodo_ini = inicio.strftime("%d/%m/%Y")
    periodo_fim = date.today().strftime("%d/%m/%Y")

    linhas_docs = ""
    for i, d in enumerate(exibir):
        gestao     = fmt_gestao(d["COGESTAO"])
        ug         = str(d["COUG"])
        noug       = (d["NOUG"]     or "").strip()
        nogest     = (d["NOGESTAO"] or "").strip()
        nudoc      = str(d["NUDOCUMENTO"] or "")
        data_lanc  = str(d["DATA"] or "—")
        div        = d["DIV"]
        bg         = "#f9f9f9" if i % 2 else "#ffffff"
        emit_label = f"{gestao}-{ug}"
        emit_nome  = f"{nogest} · {noug}" if nogest else noug
        linhas_docs += f"""
    <tr style="background:{bg}">
      <td style="font-family:monospace;white-space:nowrap">{emit_label}</td>
      <td style="font-size:11px;color:#6b7a99">{emit_nome}</td>
      <td style="font-family:monospace;white-space:nowrap">{nudoc}</td>
      <td style="white-space:nowrap;color:#6b7a99">{data_lanc}</td>
      <td style="text-align:right;font-family:monospace;color:{cor_valor(div)}">
        <b>{brl(div)}</b>
      </td>
    </tr>"""

    nota_restantes = ""
    if restantes > 0:
        nota_restantes = (f'<p style="font-size:11px;color:#6b7a99;margin-top:4px">'
                          f'Exibindo {len(exibir)} de {n_docs} documentos.</p>')

    tabela_docs = f"""
<table border="1" cellpadding="7" cellspacing="0"
       style="border-collapse:collapse;font-size:12px;width:100%">
  <thead style="background:#162550;color:#c8d8ec">
    <tr>
      <th style="text-align:left;white-space:nowrap">Gestão-UG Emitente</th>
      <th style="text-align:left">Nome</th>
      <th style="text-align:left;white-space:nowrap">Nº Documento</th>
      <th style="text-align:left;white-space:nowrap">Data Lanç.</th>
      <th style="text-align:right;white-space:nowrap">Divergência</th>
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
{nota_restantes}"""

    return f"""<html><body style="font-family:Arial,sans-serif;font-size:13px;color:#1a2033;max-width:900px">
<p>Prezados,</p>
<p>O monitoramento automático das transferências intragovernamentais identificou
divergência entre as contas 351120XXX (VPD — Transferências Intragovernamentais
Concedidas) e 451120XXX (VPA — Transferências Intragovernamentais Recebidas).</p>

<h3 style="font-size:13px;margin-bottom:6px;color:#0d1b3e">Documentos que contribuem para a divergência</h3>
{tabela_docs}

<br>
<p style="font-size:11px;color:#6b7a99">
  Esta mensagem é gerada automaticamente. Verificação realizada em {data_hora}.
</p>
</body></html>"""


# ─── Envio ou pré-visualização ────────────────────────────────────────────────
def enviar_ou_preview(html, dry_run):
    data_hoje = date.today().strftime("%d/%m/%Y")
    assunto   = (f"ALERTA CONTÁBIL — Transferências Intragovernamentais "
                 f"(351120 × 451120) — {data_hoje}")
    if dry_run:
        preview = PASTA / "transferencias_intra_email_preview.html"
        preview.write_text(html, encoding="utf-8")
        print(f"  [DRY-RUN] E-mail salvo: {preview}")
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
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Não envia e-mail; salva pré-visualização em arquivo")
    a = p.parse_args()

    inicio    = INICIO_MONITORAMENTO
    data_hora = datetime.now().strftime("%d/%m/%Y às %H:%M")

    print(f"[{datetime.now():%H:%M:%S}] Verificando transferências intragovernamentais "
          f"(351120 × 451120, a partir de {inicio.strftime('%d/%m/%Y')})…")

    oracledb.init_oracle_client(lib_dir=INSTANT_CLIENT_DIR)

    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS,
                          dsn=ORACLE_DSN) as conn:
        cur = conn.cursor()

        # 1. Verificação global
        cur.execute(SQL_GLOBAL, inicio=inicio)
        r     = cur.fetchone()
        s351  = float(r[0] or 0)
        s451  = float(r[1] or 0)
        div   = round(s351 - s451, 2)

        print(f"  Saldo 351120XXX: {brl(s351)}")
        print(f"  Saldo 451120XXX: {brl(s451)}")
        print(f"  Divergência:     {brl(div)}")

        if abs(div) <= 0.01:
            print("  [OK] Equação equilibrada. Nenhum e-mail enviado.")
            return

        # 2. Detalhamento por documento
        print("  [!] Divergência detectada. Buscando documentos…")
        cur.execute(SQL_DOCS, inicio=inicio)
        cols = [c[0] for c in cur.description]
        docs = [dict(zip(cols, row)) for row in cur.fetchall()]
        print(f"  {len(docs)} documento(s) com divergência individual.")
        for d in docs:
            print(f"    {fmt_gestao(d['COGESTAO'])}-{d['COUG']}  "
                  f"{d['NUDOCUMENTO']}  {brl(d['DIV'])}")

    html = montar_email_html(s351, s451, docs, data_hora, inicio)
    enviar_ou_preview(html, a.dry_run)
    print("  Concluído.")


if __name__ == "__main__":
    main()
