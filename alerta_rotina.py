"""
Aviso por e-mail das falhas da rotina diaria dos paineis (atualizar_paineis.ps1).

Le, do dia:
  logs/falhas_AAAAMMDD.txt           - scripts que falharam (gravado pela rotina)
  logs/alertas_extracao_AAAAMMDD.txt - extracoes suspeitas mantidas pela protecao_extracao.py
So envia e-mail se houver algo; sem falhas/alertas nao envia nada.

Uso:
    python alerta_rotina.py            # envia (se houver o que avisar)
    python alerta_rotina.py --dry-run  # so mostra o texto
"""

import argparse
import base64
import html
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

PASTA = Path(__file__).resolve().parent
TOKEN = PASTA / "gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
REMETENTE = "contabilidadegeraldodf@gmail.com"
DESTINATARIOS = ["clarissa.barbosa@economia.df.gov.br"]


def ler(nome):
    p = PASTA / "logs" / nome
    if not p.exists():
        return []
    return [l.rstrip() for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--teste", action="store_true", help="envia um e-mail de teste (nao e falha real)")
    a = ap.parse_args()
    hoje = f"{datetime.now():%Y%m%d}"
    falhas, alertas = ler(f"falhas_{hoje}.txt"), ler(f"alertas_extracao_{hoje}.txt")
    if a.teste:
        falhas = ["TESTE DO AVISO AUTOMÁTICO: nenhuma falha real. Se você recebeu esta mensagem, o envio está funcionando."]
        alertas = []
    if not falhas and not alertas:
        print("Rotina sem falhas ou alertas; nenhum e-mail enviado.")
        return

    def bloco(titulo, linhas):
        if not linhas:
            return ""
        itens = "".join(f"<li>{html.escape(l)}</li>" for l in linhas)
        return f"<h3>{titulo}</h3><ul>{itens}</ul>"

    corpo = (
        f"<p>Rotina diária dos painéis — {datetime.now():%d/%m/%Y %H:%M}.</p>"
        + bloco("Scripts que falharam", falhas)
        + bloco("Extrações suspeitas (arquivo anterior mantido)", alertas)
        + "<p>Detalhes em <code>logs/atualizacao_" + hoje + ".log</code>. "
          "Os painéis dependentes de scripts que falharam não foram atualizados.</p>"
    )
    assunto = ("[TESTE] " if a.teste else "") + f"FALHA NA ROTINA DOS PAINÉIS — {datetime.now():%d/%m/%Y}"
    if a.dry_run:
        print("Assunto:", assunto, "\nDestinatários:", ", ".join(DESTINATARIOS))
        print("\n".join(falhas + alertas))
        return
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    service = build("gmail", "v1", credentials=creds)
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = assunto, REMETENTE, ", ".join(DESTINATARIOS)
    msg.attach(MIMEText(corpo, "html", "utf-8"))
    service.users().messages().send(userId="me", body={"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}).execute()
    print(f"Aviso enviado para: {', '.join(DESTINATARIOS)}")


if __name__ == "__main__":
    main()
