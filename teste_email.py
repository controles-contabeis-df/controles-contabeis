"""
Teste de envio de e-mail institucional via Gmail API — contabilidadegeraldodf@gmail.com
"""
import os
import base64
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

PASTA = Path(__file__).parent
TOKEN = PASTA / "gmail_token.json"

REMETENTE    = "contabilidadegeraldodf@gmail.com"
DESTINATARIO = "clarissa.barbosa@economia.df.gov.br"

# Dados fictícios para o teste
lancamentos_teste = [
    {
        "COGESTAOCONTAB": "19202",
        "COUGCONTAB":     "19202",
        "COCONTACONTABIL":"821120100",
        "NUDOCUMENTO":    "2026NL000123",
        "COUG":           "19202",
        "DALANCAMENTO":   "10/07/2026",
        "VALANCAMENTO":   150000.00,
    }
]

def formatar_valor(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

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
          <td>{r['COUG']}</td>
          <td>{r['DALANCAMENTO']}</td>
          <td style="text-align:right">{formatar_valor(r['VALANCAMENTO'])}</td>
        </tr>"""

    return f"""
<html><body style="font-family:Arial,sans-serif;font-size:13px;color:#1a2033">
<p>Prezados,</p>
<p>O monitoramento automático do SIGGO identificou lançamentos em contas fora do
intervalo orçamentário nas gestões de empresas independentes.</p>
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

def enviar(destinatario, assunto, html):
    creds = Credentials.from_authorized_user_file(str(TOKEN), ["https://www.googleapis.com/auth/gmail.send"])
    service = build("gmail", "v1", credentials=creds)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"]    = REMETENTE
    msg["To"]      = destinatario
    msg.attach(MIMEText(html, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"E-mail enviado para {destinatario}")

data_hoje = datetime.now().strftime("%d/%m/%Y")
assunto   = f"[TESTE] ALERTA CONTABIL — Conta nao orcamentaria em Empresa Independente · {data_hoje}"
html      = montar_html(lancamentos_teste)
enviar(DESTINATARIO, assunto, html)
