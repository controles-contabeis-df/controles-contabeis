"""
Verificação dos lançamentos intragovernamentais — GDF consolidado (MCASP, subtítulo 2)

Realiza dois passos sobre VLANCAMENTOCONTABIL (INMES 0–mes, MIL2026):

  Passo 1 — Equação patrimonial intra
    Classes 1+3 (natureza devedora) = Classes 2+4 (natureza credora)
    Divergência = saldo devedor intra − saldo credor intra

  Passo 2 — Equilíbrio da DVP intra
    Classe 3 (VPD intra) = Classe 4 (VPA intra)
    Divergência = VPD intra − VPA intra

Envia e-mail de alerta quando qualquer |divergência| > R$ 0,01.

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

# ─── Passo 1: (Classes 1+3) vs (Classes 2+4) — subtítulo 2 ───────────────────
# Classes 1 e 3 têm natureza devedora: débito é positivo, crédito é negativo.
# Classes 2 e 4 têm natureza credora : crédito é positivo, débito é negativo.
# Ambos devem ser iguais (equação fundamental para as contas intra).
SQL_PASSO1 = """
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

# ─── Passo 2: Classe 3 (VPD) vs Classe 4 (VPA) — subtítulo 2 ────────────────
SQL_PASSO2 = """
SELECT
  SUM(CASE WHEN SUBSTR(TO_CHAR(COCONTACONTABIL),1,1) = '3'
           THEN CASE WHEN INDEBITOCREDITO='D' THEN  VALANCAMENTO
                     WHEN INDEBITOCREDITO='C' THEN -VALANCAMENTO
                     ELSE 0 END
           ELSE 0 END) AS SALDO_VPD_INTRA,
  SUM(CASE WHEN SUBSTR(TO_CHAR(COCONTACONTABIL),1,1) = '4'
           THEN CASE WHEN INDEBITOCREDITO='C' THEN  VALANCAMENTO
                     WHEN INDEBITOCREDITO='D' THEN -VALANCAMENTO
                     ELSE 0 END
           ELSE 0 END) AS SALDO_VPA_INTRA
FROM MIL2026.VLANCAMENTOCONTABIL
WHERE INMES <= :mes
  AND SUBSTR(TO_CHAR(COCONTACONTABIL),1,1) IN ('3','4')
  AND SUBSTR(TO_CHAR(COCONTACONTABIL),5,1) = '2'
"""


# ─── Formatação ───────────────────────────────────────────────────────────────
def brl(v):
    if v is None:
        return "—"
    sinal = "-" if v < 0 else ""
    return f"{sinal}R$ {abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def badge_ok():
    return '<span style="background:#1a7a44;color:#fff;padding:2px 10px;border-radius:4px;font-size:11px">✓ OK</span>'

def badge_alerta():
    return '<span style="background:#c0392b;color:#fff;padding:2px 10px;border-radius:4px;font-size:11px">✗ DIVERGÊNCIA</span>'

def status_badge(valor):
    return badge_ok() if abs(valor or 0) <= 0.01 else badge_alerta()


# ─── HTML do e-mail ───────────────────────────────────────────────────────────
def montar_email_html(mes, ano, dev, cred, vpd, vpa):
    data_hora = datetime.now().strftime("%d/%m/%Y às %H:%M")
    mes_label = f"{MESES[mes]}/{ano}"
    div1 = dev - cred
    div2 = vpd - vpa

    github_user = os.environ.get("GITHUB_USER", "")
    github_repo = os.environ.get("GITHUB_REPO", "")
    panel_url   = f"https://{github_user}.github.io/{github_repo}/intra_lancamento.html"

    return f"""<html><body style="font-family:Arial,sans-serif;font-size:13px;color:#1a2033;max-width:900px">
<p>Prezados,</p>
<p>O monitoramento automático dos lançamentos intragovernamentais do GDF referente a
{mes_label} identificou a(s) divergência(s) a seguir apontada(s) que requerem atenção.</p>

<table border="1" cellpadding="8" cellspacing="0"
       style="border-collapse:collapse;font-size:12px;width:100%">
  <thead style="background:#0d1b3e;color:#fff">
    <tr><th style="width:42%">Verificação</th><th>Resultado</th><th style="width:120px">Status</th></tr>
  </thead>
  <tbody>
    <tr>
      <td><b>Equação A — Classes 1+3 = Classes 2+4 (subtítulo 2)</b></td>
      <td>Saldo devedor intra (1+3): <b>{brl(dev)}</b><br>
          Saldo credor  intra (2+4): <b>{brl(cred)}</b><br>
          Divergência: <b>{brl(div1)}</b></td>
      <td>{status_badge(div1)}</td>
    </tr>
    <tr>
      <td><b>Equação B — Classe 3 = Classe 4 (subtítulo 2)</b></td>
      <td>VPD intra (classe 3): <b>{brl(vpd)}</b><br>
          VPA intra (classe 4): <b>{brl(vpa)}</b><br>
          Divergência: <b>{brl(div2)}</b></td>
      <td>{status_badge(div2)}</td>
    </tr>
  </tbody>
</table>

<p style="font-size:12px;color:#1a2033;margin-top:16px">
  Para análise detalhada dos lançamentos que originaram as divergências, acesse o painel de controle:<br>
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

        print(f"  Passo 1 — Classes 1+3 vs 2+4, subtítulo 2 (INMES 0–{a.mes})…")
        cur.execute(SQL_PASSO1, mes=a.mes)
        r1   = cur.fetchone()
        dev  = float(r1[0] or 0)
        cred = float(r1[1] or 0)
        div1 = dev - cred

        print(f"  Passo 2 — Classe 3 vs Classe 4, subtítulo 2 (INMES 0–{a.mes})…")
        cur.execute(SQL_PASSO2, mes=a.mes)
        r2   = cur.fetchone()
        vpd  = float(r2[0] or 0)
        vpa  = float(r2[1] or 0)
        div2 = vpd - vpa

        print(f"  Saldo devedor intra (1+3): {brl(dev)}")
        print(f"  Saldo credor  intra (2+4): {brl(cred)}")
        print(f"  Divergência Passo 1:       {brl(div1)}")
        print(f"  VPD intra (classe 3):      {brl(vpd)}")
        print(f"  VPA intra (classe 4):      {brl(vpa)}")
        print(f"  Divergência Passo 2:       {brl(div2)}")

        if abs(div1) <= 0.01 and abs(div2) <= 0.01:
            print("  [OK] Nenhuma divergência detectada. Nenhum e-mail enviado.")
            return

        print("  [!] Divergência detectada — gerando alerta…")

    html = montar_email_html(a.mes, a.ano, dev, cred, vpd, vpa)
    enviar_ou_preview(html, a.mes, a.ano, a.dry_run)
    print("  Concluído.")


if __name__ == "__main__":
    main()
