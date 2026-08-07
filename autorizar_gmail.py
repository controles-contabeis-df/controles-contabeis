"""
Autorização única da conta Gmail via OAuth2.
Execute este script uma vez — ele abrirá o navegador para autorizar.
O token será salvo em gmail_token.json e reutilizado automaticamente.
"""
import os
from google_auth_oauthlib.flow import InstalledAppFlow
from pathlib import Path

PASTA = Path(__file__).parent
CREDS = PASTA / "gmail_credentials.json"
TOKEN = PASTA / "gmail_token.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

flow = InstalledAppFlow.from_client_secrets_file(str(CREDS), SCOPES)
creds = flow.run_local_server(port=0)

TOKEN.write_text(creds.to_json())
print(f"\nAutorização concluída! Token salvo em: {TOKEN}")
