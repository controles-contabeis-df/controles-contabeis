"""
Monitoramento diário de lançamentos em contas não orçamentárias
nas gestões de empresas independentes (INTIPOADM = '6').

Envia e-mail de alerta apenas quando anomalias são encontradas.
"""
import os
import base64
import oracledb
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from dotenv import load_dotenv

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

PASTA = Path(__file__).parent
load_dotenv(PASTA / ".env")

TOKEN = PASTA / "gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

ORACLE_USER = os.environ["ORACLE_USER"]
ORACLE_PASS = os.environ["ORACLE_PASS"]
ORACLE_DSN  = os.environ["ORACLE_DSN"]

REMETENTE    = "contabilidadegeraldodf@gmail.com"
DESTINATARIOS = [
    "leonardo.marchioni@economia.df.gov.br",
    "clarissa.barbosa@economia.df.gov.br",
    "daniel.mello@economia.df.gov.br",
]

SQL = """
SELECT
    v.COGESTAOCONTAB,
    v.COUGCONTAB,
    v.COCONTACONTABIL,
    v.NUDOCUMENTO,
    v.COUG        AS UG_EMITENTE,
    v.DALANCAMENTO,
    v.VALANCAMENTO
FROM MIL2026.VLANCAMENTOCONTABIL v
JOIN MIL2026.GESTAO g
    ON g.COGESTAO = v.COGESTAOCONTAB
WHERE g.INTIPOADM = '6'
  AND (
        CAST(v.COCONTACONTABIL AS VARCHAR2(20)) NOT LIKE '5%'
    AND CAST(v.COCONTACONTABIL AS VARCHAR2(20)) NOT LIKE '6%'
  )
ORDER BY v.DALANCAMENTO
"""

def formatar_valor(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def formatar_data(d):
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    return d.strftime("%d/%m/%Y")

def montar_html(lancamentos):
    data_hora = datetime.now().strftime("%d/%m/%Y às %H:%M")
    linhas = ""
    for r in lancamentos:
        linhas += f"""
        <tr>
          <td>{r['COGESTAOCONTAB']}</td>
          <td>{r['COUGCONTAB']}</td>
          <td>{r['COCONTACONTABIL']}</td>
          <td>{r['NUDOCUMENTO']}</td>
          <td>{r['UG_EMITENTE']}</td>
          <td>{formatar_data(r['DALANCAMENTO'])}</td>
          <td style="text-align:right">{formatar_valor(r['VALANCAMENTO'])}</td>
        </tr>"""

    qtd = len(lancamentos)
    return f"""
<html><body style="font-family:Arial,sans-serif;font-size:13px;color:#1a2033">
<p>Prezados,</p>
<p>O monitoramento automático do SIGGO identificou <strong>{qtd} lançamento(s)</strong>
em contas fora do intervalo orçamentário (5XXXXXXXX ou 6XXXXXXXX)
nas gestões de empresas independentes.</p>
<p><strong>Lançamentos identificados:</strong></p>
<table border="1" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;font-size:12px;width:100%">
  <thead style="background:#0d1b3e;color:#fff">
    <tr>
      <th>Gestão Contábil</th>
      <th>UG Contábil</th>
      <th>Conta Contábil</th>
      <th>Nº Documento</th>
      <th>UG Emitente</th>
      <th>Data Lançamento</th>
      <th>Valor</th>
    </tr>
  </thead>
  <tbody>{linhas}
  </tbody>
</table>
<br>
<p style="font-size:11px;color:#6b7a99">
  Esta mensagem é gerada automaticamente pelo sistema de controles contábeis da CONTDF/GDF.<br>
  Verificação realizada em {data_hora}.
</p>
</body></html>"""

def enviar_email(lancamentos):
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    service = build("gmail", "v1", credentials=creds)

    data_hoje = datetime.now().strftime("%d/%m/%Y")
    assunto = f"ALERTA CONTABIL — Conta nao orcamentaria em Empresa Independente · {data_hoje}"
    html = montar_html(lancamentos)

    destinatarios_str = ", ".join(DESTINATARIOS)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"]    = REMETENTE
    msg["To"]      = destinatarios_str
    msg.attach(MIMEText(html, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"Alerta enviado para: {destinatarios_str}")

def main():
    print(f"[{datetime.now():%H:%M:%S}] Verificando empresas independentes...")

    oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN)
    cur  = conn.cursor()
    cur.execute(SQL)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    cur.close()
    conn.close()

    print(f"Lançamentos encontrados: {len(rows)}")

    if rows:
        enviar_email(rows)
    else:
        print("Nenhuma anomalia detectada. Nenhum e-mail enviado.")

if __name__ == "__main__":
    main()
