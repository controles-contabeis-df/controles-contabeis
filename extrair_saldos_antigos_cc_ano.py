"""
Saldos Antigos por Ano da Conta Corrente (Classes 1 e 2) — SIGGO/Oracle

Lista os saldos de contas patrimoniais de Ativo (classe 1) e Passivo (classe 2) cuja
conta corrente (COCONTACORRENTE) informa um ANO igual ou anterior ao ano de corte
(exercício - 5 anos, ou seja, até 2021 em 2026): possíveis créditos/obrigações com mais
de 5 anos, a regularizar (baixa, prescrição, cancelamento).

O ano só existe em alguns tipos de conta corrente (VCONTACONTABIL.INCONTACORRENTE).
Regras de extração do ano — validadas em 26/11/2026 contra os dados reais:

  Tipo  Layout (catálogo SIGGO)              Onde está o ano
  ----  ------------------------------------ ----------------------------------------
  04    Ano + CGC/CPF/IG/UG-Gestão            posições 1-4 (ignora formato antigo de 11/14 dígitos, sem ano)
  12    Ano + Fonte de Recurso                posições 1-4 (+ 9 dígitos de fonte)
  63    NE + Fonte NE + Credor                posições 1-4 da NE (AAAANE00000)
  76    NE + Fonte + UG/Gestão                posições 1-4 da NE
  16/39/42/52/64  NE na frente                posições 1-4 da NE (mesma regra)
  06    Ordem Bancária                        posições 1-4 (AAAAOB00000)
  67    Fonte + Ano + Credor                  posições 10-13 (formato antigo de 18 caracteres: posições 1-4)
  45    Nº Processo + CGC/CPF...              ano do processo, após a barra (.../AAAA-DV) — ano INFERIDO
  00    Nulo                                  só quando o conteúdo tem padrão de documento (AAAANE00000)

Tipos do catálogo que trazem ano mas ainda NÃO têm regra implementada (15, 21, 31, 57): se
aparecerem com saldo, o script avisa no log para mapear o layout.

Uso:
    python extrair_saldos_antigos_cc_ano.py               (gera o HTML local, NÃO publica)
    python extrair_saldos_antigos_cc_ano.py --push        (gera e publica no GitHub Pages)
    python extrair_saldos_antigos_cc_ano.py --ano-corte 2020
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import oracledb
import pandas as pd
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ORACLE_USER = os.environ["ORACLE_USER"]
ORACLE_PASS = os.environ["ORACLE_PASS"]
ORACLE_DSN = os.environ["ORACLE_DSN"]
SCHEMA = "MIL2026."

ANO_EXERCICIO = 2026
ANOS_LIMITE = 5
ANO_CORTE = ANO_EXERCICIO - ANOS_LIMITE  # 2021: ano <= corte => mais de 5 anos

ARQUIVO_HTML = "saldos_antigos_cc_ano.html"

# ── Regras de extração do ano ───────────────────────────────────────────────────
_ANO = r"(?:19|20)\d{2}"
RE_NE_NA_FRENTE = re.compile(rf"^({_ANO})NE\d")
RE_OB_NA_FRENTE = re.compile(rf"^({_ANO})OB\d")
RE_DOC_GENERICO = re.compile(rf"^({_ANO})(?:NE|OB|NL|PE)\d")
RE_ANO_INICIO = re.compile(rf"^({_ANO})")
RE_ANO_PROCESSO = re.compile(rf"/({_ANO})-")

TIPOS_NE_NA_FRENTE = {"16", "39", "42", "52", "63", "64", "76"}
TIPOS_COM_ANO_SEM_REGRA = {"15": "Categoria + Ano + Mês", "21": "UG + Célula + Ano + Mês",
                           "31": "UO + Célula Financeira + Ano + Mês", "57": "Código Receita + Ano + CGC/CPF/IG/UG-Gestão"}

TIPO_ROTULO = {
    "00": "00 · Nulo (documento NE no padrão)",
    "04": "04 · Ano + CGC/CPF/IG/UG-Gestão",
    "06": "06 · Ordem Bancária",
    "12": "12 · Ano + Fonte de Recurso",
    "16": "16 · Número do Empenho",
    "39": "39 · NE + Subelemento",
    "42": "42 · NE + Natureza da Despesa",
    "45": "45 · Nº Processo + CGC/CPF… (ano do processo)",
    "52": "52 · NE + Fonte + Credor",
    "63": "63 · NE + Fonte NE + Credor",
    "64": "64 · NE + Número da Transferência",
    "67": "67 · Fonte + Ano + Credor",
    "76": "76 · NE + Fonte + UG/Gestão",
}

ORIGEM_EXPLICITA = "Ano na conta corrente"
ORIGEM_PROCESSO = "Ano inferido do nº do processo"
ORIGEM_DOCUMENTO = "Ano do documento (NE/OB)"


def extrair_ano(tipo: str, cc: str):
    """Retorna (ano:int|None, origem:str|None) para um COCONTACORRENTE já sem espaços à direita."""
    m = None
    origem = ORIGEM_EXPLICITA
    if tipo == "04":
        # formato antigo (só CNPJ=14 ou CPF/UG-Gestão=11 dígitos) não tem ano; o ano nunca vem sozinho nesses
        if len(cc) not in (11, 14):
            m = RE_ANO_INICIO.match(cc)
    elif tipo == "12":
        m = re.match(rf"^({_ANO})\d{{9}}", cc)
    elif tipo in TIPOS_NE_NA_FRENTE:
        m = RE_NE_NA_FRENTE.match(cc)
        origem = ORIGEM_DOCUMENTO
    elif tipo == "06":
        m = RE_OB_NA_FRENTE.match(cc)
        origem = ORIGEM_DOCUMENTO
    elif tipo == "67":
        if len(cc) == 18:                       # formato antigo: Ano + Credor (sem fonte)
            m = RE_ANO_INICIO.match(cc)
        elif len(cc) >= 13:
            m = re.match(rf"^\d{{9}}({_ANO})", cc)
    elif tipo == "45":
        m = RE_ANO_PROCESSO.search(cc)
        origem = ORIGEM_PROCESSO
    else:
        # sem regra para o tipo: só aceita padrão inequívoco de documento (ex.: 2012NE00126 em conta tipo 00)
        m = RE_DOC_GENERICO.match(cc)
        origem = ORIGEM_DOCUMENTO
    if not m:
        return None, None
    ano = int(m.group(1))
    if ano > ANO_EXERCICIO:
        return None, None
    return ano, origem


# ── Proteção de dados pessoais (LGPD) ───────────────────────────────────────────
# Documentos de 11 dígitos (CPF; podem coincidir com UG+Gestão, então tudo de 11 dígitos é tratado)
# são substituídos por pseudônimo: HMAC-SHA256 com chave secreta que fica no .env
# (ANONIMIZACAO_CHAVE), fora deste código, do HTML e do repositório. Sem a chave não há como
# recalcular/inverter o pseudônimo; o mesmo documento gera sempre o mesmo pseudônimo.
RE_DOC11_ISOLADO = re.compile(r"(?<!\d)\d{11}(?!\d)")
RE_PSEUDONIMO = re.compile(r"\[DOC11-[0-9A-F]{10}\]")
RE_DOC_APOS_PROCESSO = re.compile(rf"/{_ANO}-\d{{2}}(\d+)$")
MASCARA_FORMATO = "[DOC11-XXXXXXXXXX]"
TAMANHO_PSEUDONIMO = 10  # caracteres hexadecimais (40 bits)


def carregar_chave_anonimizacao() -> bytes:
    """Lê ANONIMIZACAO_CHAVE do .env; se não existir, gera uma chave aleatória e a grava no .env."""
    chave = os.environ.get("ANONIMIZACAO_CHAVE", "").strip()
    if not chave:
        chave = secrets.token_hex(32)
        env = Path(__file__).parent / ".env"
        texto = env.read_text(encoding="utf-8") if env.exists() else ""
        sep = "" if texto.endswith("\n") or not texto else "\n"
        env.write_text(
            texto + sep + "\n# Chave secreta de pseudonimizacao de CPF nos paineis (NUNCA versionar, NUNCA publicar)\n"
            f"ANONIMIZACAO_CHAVE={chave}\n", encoding="utf-8")
        os.environ["ANONIMIZACAO_CHAVE"] = chave
        print("  Chave de anonimizacao criada e gravada no .env (guarde uma copia segura).")
    return chave.encode("utf-8")


def _pseudonimo(chave: bytes, documento: str) -> str:
    h = hmac.new(chave, documento.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"[DOC11-{h[:TAMANHO_PSEUDONIMO].upper()}]"


def _segmento_documento(tipo: str, cc: str):
    """Posição (ini, fim) onde fica o documento do credor/devedor, conforme o layout do tipo."""
    if tipo == "04" and len(cc) > 4:
        return 4, len(cc)
    if tipo == "67":
        return (4, len(cc)) if len(cc) == 18 else (13, len(cc))
    if tipo in ("63", "52"):
        return 20, len(cc)
    if tipo == "45":
        m = RE_DOC_APOS_PROCESSO.search(cc)
        if m:
            return m.span(1)
    return None


def pseudonimizar_cc(chave: bytes, tipo: str, cc: str) -> str:
    seg = _segmento_documento(tipo, cc)
    if seg:
        ini, fim = seg
        if re.fullmatch(r"\d{11}", cc[ini:fim]):
            cc = cc[:ini] + _pseudonimo(chave, cc[ini:fim]) + cc[fim:]
    # rede de segurança: qualquer sequência isolada de 11 dígitos remanescente
    return RE_DOC11_ISOLADO.sub(lambda m: _pseudonimo(chave, m.group(0)), cc)


def verificar_sem_documentos(serie: pd.Series) -> int:
    """Falha se sobrar documento de 11 dígitos em claro. Devolve a quantidade de linhas pseudonimizadas."""
    limpo = serie.str.replace(RE_PSEUDONIMO, "", regex=True)
    restos = limpo[limpo.str.contains(RE_DOC11_ISOLADO)]
    if len(restos):
        raise RuntimeError(f"{len(restos)} conta(s) corrente(s) ainda com documento de 11 digitos em claro — publicacao abortada.")
    return int(serie.str.contains(RE_PSEUDONIMO).sum())


# ── SQL ─────────────────────────────────────────────────────────────────────────
SQL_SALDOS = f"""
SELECT
    cc.INCONTACORRENTE                  AS TIPO,
    s.COCONTACONTABIL                   AS CONTA_CONTABIL,
    s.COGESTAO,
    s.COUG,
    s.COCONTACORRENTE                   AS CONTA_CORRENTE,
    SUM(s.VADEBITO)                     AS DEBITO,
    SUM(s.VACREDITO)                    AS CREDITO
FROM {SCHEMA}VSALDOCONTABIL s
JOIN {SCHEMA}VCONTACONTABIL cc ON cc.COCONTACONTABIL = s.COCONTACONTABIL
WHERE SUBSTR(s.COCONTACONTABIL, 1, 1) IN ('1', '2')
GROUP BY cc.INCONTACORRENTE, s.COCONTACONTABIL, s.COGESTAO, s.COUG, s.COCONTACORRENTE
HAVING ABS(SUM(s.VADEBITO) - SUM(s.VACREDITO)) >= 0.005
"""

SQL_UGS = f"""
SELECT COUG, NOUG FROM {SCHEMA}UNIDADEGESTORA WHERE COUG <> '0' AND NOUG NOT LIKE '%TESTE%'
"""
SQL_GESTOES = f"SELECT COGESTAO, NOGESTAO FROM {SCHEMA}GESTAO"
SQL_CONTAS = f"""
SELECT COCONTACONTABIL, NOCONTACONTABIL FROM {SCHEMA}VCONTACONTABIL
WHERE SUBSTR(COCONTACONTABIL, 1, 1) IN ('1', '2')
"""
SQL_MES = f"SELECT MAX(INMES) AS MES FROM {SCHEMA}VSALDOCONTABIL WHERE INMES < 13"


# ── HTML template (placeholders __X__; sem escape de chaves) ────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Saldos Antigos - Ativos e Passivos</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --navy:#0d1b3e;--navy-mid:#162550;--navy-light:#1e3267;
  --teal:#0090a8;--teal-light:#00b8d4;
  --surface:#fff;--bg:#f2f5f9;--border:#dce3ed;
  --row-alt:#f7f9fc;--hover:#eaf4f7;
  --text:#1a2033;--muted:#6b7a99;
  --red:#c0392b;--green:#1a7a44;--amber:#d98a00;--radius:10px;
  --shadow:0 2px 12px rgba(13,27,62,.10);
}
body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}
header{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}
.hlogo{width:32px;height:32px;background:var(--teal);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}
header h1{font-size:14px;font-weight:700;letter-spacing:.6px;text-transform:uppercase}
header h1 span{font-weight:400;color:#9ab0cc;font-size:12px;display:block;text-transform:none;letter-spacing:0;margin-top:1px}
#ts{font-size:11px;color:#7a99bb;white-space:nowrap}
.aviso{background:#fff8e6;border-bottom:1px solid #f0c060;padding:8px 28px;font-size:11.5px;color:#7a5c00;display:flex;align-items:flex-start;gap:8px;line-height:1.5}
.aviso details{flex:1}
.aviso summary{cursor:pointer;font-weight:600}
.aviso ul{margin:6px 0 2px 18px}
.aviso li{margin-bottom:3px}
.aviso strong{color:#5a3e00}
.fbar{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
.fg{display:flex;flex-direction:column;gap:4px}
.fg label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.fg select{border:1.5px solid var(--border);border-radius:6px;padding:7px 28px 7px 10px;font-size:12.5px;min-width:130px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E") no-repeat right 9px center;color:var(--text);cursor:pointer;appearance:none;max-width:300px}
.fg select:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-wrap{position:relative;min-width:200px}
.ac-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text)}
.ac-input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}
.ac-dd{position:absolute;top:calc(100% + 4px);left:0;right:0;min-width:280px;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:240px;overflow-y:auto;display:none}
.ac-dd-item{padding:8px 12px;cursor:pointer;font-size:12.5px;border-bottom:1px solid var(--border)}
.ac-dd-item:last-child{border-bottom:none}
.ac-dd-item:hover{background:var(--hover)}
.ac-dd-item strong{color:var(--navy);font-weight:700}
.ac-dd-empty{padding:12px;color:var(--muted);font-size:12px;text-align:center}
.bgrp{display:flex;gap:8px;margin-left:auto;align-items:flex-end;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;transition:filter .15s,transform .1s;white-space:nowrap}
.btn:hover{filter:brightness(1.08);transform:translateY(-1px)}
.btn-p{background:var(--teal);color:#fff}
.btn-g{background:var(--border);color:var(--text)}
.krow{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;padding:18px 28px 4px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}
.kpi.ka::before{background:linear-gradient(90deg,var(--red),#e74c3c)}
.kpi.kw::before{background:linear-gradient(90deg,#f0a500,#ffcc44)}
.kl{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.kv{font-size:17px;font-weight:700;letter-spacing:-.3px;line-height:1}
.ks{font-size:11px;color:var(--muted);margin-top:5px}
.chart{margin:14px 28px 0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);padding:14px 18px 10px}
.chart h2{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px}
.chart h2 small{font-weight:400;text-transform:none;letter-spacing:0}
.leg{display:flex;gap:14px;font-size:11px;color:var(--muted);font-weight:400;text-transform:none;letter-spacing:0}
.leg i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.bars{display:flex;align-items:flex-end;gap:6px;height:150px;overflow-x:auto;padding-bottom:2px}
.bcol{flex:1 0 34px;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;cursor:pointer;min-width:34px}
.bcol:hover .bpair{filter:brightness(1.1)}
.bcol.sel .by{font-weight:800;color:var(--navy)}
.bpair{display:flex;align-items:flex-end;gap:2px;width:100%;justify-content:center;flex:1}
.b{width:42%;max-width:20px;border-radius:3px 3px 0 0;min-height:1px}
.b1{background:var(--teal)}.b2{background:#e08a3c}
.by{font-size:10px;color:var(--muted);margin-top:4px}
.tsec{padding:16px 28px 32px}
.thead-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.ttitle{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.sw{position:relative}
.sw input{border:2px solid var(--teal);border-radius:6px;padding:8px 12px 8px 34px;font-size:13px;width:280px;background:#fff;box-shadow:0 0 0 3px rgba(0,144,168,.08)}
.sw input:focus{outline:none;border-color:var(--navy)}
.sw input::placeholder{color:var(--teal);font-weight:500}
.sw::before{content:'🔍';position:absolute;left:9px;top:50%;transform:translateY(-50%);font-size:13px;pointer-events:none}
.tw{border-radius:var(--radius);border:1px solid var(--border);box-shadow:var(--shadow);overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:820px}
thead th{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:left;white-space:nowrap;letter-spacing:.3px}
thead th.right{text-align:right}
tr.grp-l1{background:var(--navy);cursor:pointer}
tr.grp-l1 td{color:#e8f0fc;font-weight:700;padding:10px 14px}
tr.grp-l1:hover td{background:var(--navy-mid)}
tr.grp-l2{background:#1e3267;cursor:pointer}
tr.grp-l2 td{color:#c8d8ec;font-weight:700;padding:9px 14px 9px 28px}
tr.grp-l2:hover td{background:#162550}
tr.grp-l3{background:var(--surface)}
tr.grp-l3:nth-child(even){background:var(--row-alt)}
tr.grp-l3:hover{background:var(--hover)}
tr.grp-l3 td{padding:7px 14px 7px 46px}
tr.mais td{padding:8px 14px 8px 46px;color:var(--muted);font-style:italic;background:#fff8e6}
.tog{margin-right:6px;font-style:normal;display:inline-block;width:12px}
td{padding:9px 14px;border-bottom:1px solid var(--border);white-space:nowrap;font-variant-numeric:tabular-nums}
td.right{text-align:right}
.mono{font-family:'Consolas','Courier New',monospace;font-size:12px}
.vp{color:var(--green);font-weight:700}
.vn{color:var(--red);font-weight:700}
.vz{color:var(--muted)}
tr.grp-l1 .vp,tr.grp-l1 .vn,tr.grp-l2 .vp,tr.grp-l2 .vn{color:#fff}
tfoot td{background:#e8f0f8;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;font-size:12.5px}
.empty{text-align:center;padding:56px;color:var(--muted)}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;letter-spacing:.3px}
.bt{background:#e0f3f7;color:#00677a}.ba{background:#fdf0dc;color:#9a5b00}
.bano{background:#fde8e6;color:var(--red)}
.nota{margin:0 28px 28px;font-size:11.5px;color:var(--muted);line-height:1.6;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:12px 18px}
.nota strong{color:var(--text)}
.voltar{font-size:11px;color:#7a99bb;text-decoration:none;margin-left:20px;opacity:.8}
.voltar:hover{opacity:1}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">🕰️</div>
    <h1>Saldos Antigos - Ativos e Passivos
      <span>SIGGO · Contas patrimoniais de classes 1 e 2 com ano da conta corrente até __ANO_CORTE__ (mais de __ANOS_LIMITE__ anos)</span>
    </h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <span id="ts">Gerado em: __TIMESTAMP__</span>
</header>
<div class="aviso">
  <span>ℹ️</span>
  <details>
    <summary>Critérios de seleção — contas de classes 1 e 2 cujo ano na conta corrente é até __ANO_CORTE__ (mais de __ANOS_LIMITE__ anos), com documentos de 11 dígitos pseudonimizados</summary>
    <ul id="crit-lista"></ul>
  </details>
</div>
<div class="fbar">
  <div class="fg"><label>Classe</label>
    <select id="f-classe" onchange="aplicar()"><option value="">Todas</option><option value="1">1 · Ativo</option><option value="2">2 · Passivo</option></select></div>
  <div class="fg"><label>Ano da conta corrente</label>
    <select id="f-ano" onchange="aplicar()"></select></div>
  <div class="fg"><label>Tipo de conta corrente</label>
    <select id="f-tipo" onchange="aplicar()"></select></div>
  <div class="fg"><label>Origem do ano</label>
    <select id="f-origem" onchange="aplicar()"></select></div>
  <div class="fg"><label>Gestão</label>
    <div class="ac-wrap"><input id="fg-input" class="ac-input" type="text" placeholder="Código ou nome…" autocomplete="off" oninput="onACInput('g')" onfocus="onACFocus('g')" onblur="onACBlur('g')"><button class="ac-clear" id="fg-clear" onclick="limparAC('g')">✕</button><div class="ac-dd" id="fg-dd"></div></div></div>
  <div class="fg"><label>Unidade Gestora</label>
    <div class="ac-wrap" style="min-width:260px"><input id="fu-input" class="ac-input" type="text" placeholder="Código ou nome…" autocomplete="off" oninput="onACInput('u')" onfocus="onACFocus('u')" onblur="onACBlur('u')"><button class="ac-clear" id="fu-clear" onclick="limparAC('u')">✕</button><div class="ac-dd" id="fu-dd"></div></div></div>
  <div class="fg"><label>Conta Contábil</label>
    <div class="ac-wrap" style="min-width:280px"><input id="fc-input" class="ac-input" type="text" placeholder="Código ou nome…" autocomplete="off" oninput="onACInput('c')" onfocus="onACFocus('c')" onblur="onACBlur('c')"><button class="ac-clear" id="fc-clear" onclick="limparAC('c')">✕</button><div class="ac-dd" id="fc-dd"></div></div></div>
  <div class="bgrp">
    <button class="btn btn-g" onclick="expandirContas()">▼ Expandir UGs</button>
    <button class="btn btn-g" onclick="recolherTudo()">▲ Recolher tudo</button>
    <button class="btn btn-g" onclick="limpar()">↺ Limpar filtros</button>
    <button class="btn btn-p" onclick="exportar()">⬇ Exportar CSV</button>
  </div>
</div>
<div class="krow" id="krow"></div>
<div class="chart">
  <h2><span>Saldo por ano da conta corrente <small>· valores absolutos, altura em escala de raiz quadrada (passe o mouse para ver o valor) · clique para filtrar o ano</small></span>
    <span class="leg"><span><i style="background:var(--teal)"></i>Ativo (classe 1)</span><span><i style="background:#e08a3c"></i>Passivo (classe 2)</span></span></h2>
  <div class="bars" id="bars"></div>
</div>
<div class="tsec">
  <div class="thead-row">
    <span class="ttitle" id="cnt"></span>
    <div class="sw"><input id="busca" type="text" placeholder="Buscar conta, UG ou conta corrente…" oninput="aplicar()"></div>
  </div>
  <div class="tw">
    <table>
      <thead><tr><th>Unidade Gestora / Conta Contábil / Conta Corrente</th><th>Tipo</th><th>Ano</th><th class="right">Saldo</th></tr></thead>
      <tbody id="tbody"></tbody>
      <tfoot id="tfoot"></tfoot>
    </table>
  </div>
</div>
<script>
const ALL=__DADOS__;
const TOT=__TOTAIS__;  // saldo atual total por gestão/UG/conta (denominador dos percentuais)
const META=__META__;
let fil=[];
const expandidos=new Set();
const LIMITE_FOLHAS=300;
const brl=v=>isNaN(v)?'—':Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
const vc=v=>Math.abs(v)<0.005?'vz':v>0?'vp':'vn';
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

ALL.forEach(r=>{
  r.GESTAO_LABEL=r.COGESTAO+(r.NOGESTAO?' - '+r.NOGESTAO:'');
  r.UG_LABEL=r.COUG+' - '+(r.NOUG||'Sem nome')+' (gestão '+r.COGESTAO+')';
  r.CONTA_LABEL=r.CONTA_CONTABIL+(r.NOCONTACONTABIL?' - '+r.NOCONTACONTABIL:'');
  r.UGKEY=r.COUG+'|'+r.COGESTAO;
});

// ── selects ──
function preencherSelects(){
  const anos=[...new Set(ALL.map(r=>r.ANO))].sort((a,b)=>a-b);
  document.getElementById('f-ano').innerHTML='<option value="">Todos (≤ '+META.ano_corte+')</option>'+anos.map(a=>`<option value="${a}">${a}</option>`).join('');
  const tipos=[...new Set(ALL.map(r=>r.TIPO))].sort();
  document.getElementById('f-tipo').innerHTML='<option value="">Todos</option>'+tipos.map(t=>`<option value="${t}">${esc(META.tipos[t]||t)}</option>`).join('');
  const orig=[...new Set(ALL.map(r=>r.ORIGEM))].sort();
  document.getElementById('f-origem').innerHTML='<option value="">Todas</option>'+orig.map(o=>`<option value="${esc(o)}">${esc(o)}</option>`).join('');
}
preencherSelects();

// ── autocompletes ──
function buildList(keyFn,labelFn){const m={};ALL.forEach(r=>{const k=keyFn(r);if(k&&!m[k])m[k]=labelFn(r);});return Object.entries(m).map(([c,label])=>({c,label})).sort((a,b)=>a.c.localeCompare(b.c,'pt-BR'));}
const acState={
  g:{sel:'',list:buildList(r=>r.COGESTAO,r=>r.GESTAO_LABEL),inp:'fg-input',clr:'fg-clear',dd:'fg-dd'},
  u:{sel:'',list:buildList(r=>r.COUG,r=>r.COUG+' - '+(r.NOUG||'Sem nome')),inp:'fu-input',clr:'fu-clear',dd:'fu-dd'},
  c:{sel:'',list:buildList(r=>r.CONTA_CONTABIL,r=>r.CONTA_LABEL),inp:'fc-input',clr:'fc-clear',dd:'fc-dd'}};
function filtroAC(k){const st=acState[k],v=document.getElementById(st.inp).value.toLowerCase();return v?st.list.filter(x=>x.c.includes(v)||x.label.toLowerCase().includes(v)):st.list;}
function onACInput(k){const st=acState[k];renderACDD(k,filtroAC(k));if(!document.getElementById(st.inp).value){st.sel='';document.getElementById(st.clr).style.display='none';aplicar();}}
function onACFocus(k){renderACDD(k,filtroAC(k));}
function onACBlur(k){setTimeout(()=>document.getElementById(acState[k].dd).style.display='none',200);}
function renderACDD(k,lista){
  const dd=document.getElementById(acState[k].dd);
  if(!lista.length){dd.innerHTML='<div class="ac-dd-empty">Nenhum resultado</div>';dd.style.display='block';return;}
  dd.innerHTML=lista.slice(0,100).map((x,i)=>{
    const sep=x.label.indexOf(' - ');
    const h=sep>=0?`<strong>${esc(x.label.slice(0,sep))}</strong> — ${esc(x.label.slice(sep+3))}`:esc(x.label);
    return `<div class="ac-dd-item" data-k="${k}" data-i="${i}">${h}</div>`;}).join('');
  dd.querySelectorAll('.ac-dd-item').forEach(el=>el.onmousedown=()=>{const x=lista[+el.dataset.i];selAC(k,x.c,x.label);});
  dd.style.display='block';
}
function selAC(k,c,label){const st=acState[k];st.sel=c;document.getElementById(st.inp).value=label;document.getElementById(st.dd).style.display='none';document.getElementById(st.clr).style.display='block';aplicar();}
function limparAC(k){const st=acState[k];st.sel='';document.getElementById(st.inp).value='';document.getElementById(st.clr).style.display='none';aplicar();}

// ── filtro ──
function aplicar(){
  const b=document.getElementById('busca').value.trim().toLowerCase();
  const cl=document.getElementById('f-classe').value,an=document.getElementById('f-ano').value,
        tp=document.getElementById('f-tipo').value,og=document.getElementById('f-origem').value;
  fil=ALL.filter(r=>{
    if(cl&&r.CLASSE!==cl)return false;
    if(an&&String(r.ANO)!==an)return false;
    if(tp&&r.TIPO!==tp)return false;
    if(og&&r.ORIGEM!==og)return false;
    if(acState.g.sel&&r.COGESTAO!==acState.g.sel)return false;
    if(acState.u.sel&&r.COUG!==acState.u.sel)return false;
    if(acState.c.sel&&r.CONTA_CONTABIL!==acState.c.sel)return false;
    if(b&&!(r.CONTA_LABEL.toLowerCase().includes(b)||r.UG_LABEL.toLowerCase().includes(b)||r.CONTA_CORRENTE.toLowerCase().includes(b)))return false;
    return true;
  });
  render();kpis();grafico();
}
function limpar(){
  document.getElementById('busca').value='';
  ['f-classe','f-ano','f-tipo','f-origem'].forEach(i=>document.getElementById(i).value='');
  ['g','u','c'].forEach(k=>{const st=acState[k];st.sel='';document.getElementById(st.inp).value='';document.getElementById(st.clr).style.display='none';});
  aplicar();
}

// ── tabela (árvore Conta → UG → linhas; folhas só quando a UG é expandida) ──
function render(){
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  document.getElementById('cnt').textContent=fil.length.toLocaleString('pt-BR')+' registro'+(fil.length!==1?'s':'');
  if(!fil.length){tb.innerHTML='<tr><td colspan="4" class="empty">Nenhum registro com os filtros selecionados.</td></tr>';tf.innerHTML='';return;}
  // Árvore: Unidade Gestora → Conta Contábil → Conta Corrente
  const tree={};
  fil.forEach(r=>{
    if(!tree[r.UGKEY])tree[r.UGKEY]={label:r.UG_LABEL,a:0,p:0,n:0,ch:{}};
    const u=tree[r.UGKEY];u.n++;
    r.CLASSE==='1'?u.a+=r.SALDO:u.p+=r.SALDO;
    if(!u.ch[r.CONTA_CONTABIL])u.ch[r.CONTA_CONTABIL]={label:r.CONTA_LABEL,s:0,rows:[]};
    u.ch[r.CONTA_CONTABIL].rows.push(r);u.ch[r.CONTA_CONTABIL].s+=r.SALDO;
  });
  const peso=u=>Math.abs(u.a)+Math.abs(u.p);
  let html='';
  Object.keys(tree).sort((x,y)=>peso(tree[y])-peso(tree[x])).forEach(k=>{
    const u=tree[k],uid='u'+k,open=expandidos.has(uid);
    html+=`<tr class="grp-l1" onclick="tog('${uid}')"><td colspan="2"><span class="tog">${open?'▼':'▶'}</span>${esc(u.label)} <span style="font-weight:400;opacity:.7">· ${u.n.toLocaleString('pt-BR')} reg.</span></td><td colspan="2" class="right">Ativo ${brl(u.a)} &nbsp;·&nbsp; Passivo ${brl(u.p)}</td></tr>`;
    if(!open)return;
    Object.keys(u.ch).sort().forEach(c=>{
      const n=u.ch[c],cid=uid+'|'+c,copen=expandidos.has(cid);
      html+=`<tr class="grp-l2" onclick="tog('${cid}')"><td colspan="3"><span class="tog">${copen?'▼':'▶'}</span>${esc(n.label)} <span style="font-weight:400;opacity:.7">· ${n.rows.length.toLocaleString('pt-BR')} reg.</span></td><td class="right ${vc(n.s)}">${brl(n.s)}</td></tr>`;
      if(!copen)return;
      n.rows.sort((a,b)=>a.ANO-b.ANO||a.CONTA_CORRENTE.localeCompare(b.CONTA_CORRENTE));
      n.rows.slice(0,LIMITE_FOLHAS).forEach(r=>{
        html+=`<tr class="grp-l3"><td class="mono">${esc(r.CONTA_CORRENTE)}</td><td><span class="badge ${r.ORIGEM==='Ano na conta corrente'?'bt':'ba'}" title="${esc(r.ORIGEM)}">${esc(r.TIPO)}</span></td><td><span class="badge bano">${r.ANO}</span></td><td class="right ${vc(r.SALDO)}">${brl(r.SALDO)}</td></tr>`;
      });
      if(n.rows.length>LIMITE_FOLHAS)html+=`<tr class="mais"><td colspan="4">Mostrando ${LIMITE_FOLHAS} de ${n.rows.length.toLocaleString('pt-BR')} linhas desta conta — use os filtros ou exporte o CSV para ver todas.</td></tr>`;
    });
  });
  tb.innerHTML=html;
  const a=fil.filter(r=>r.CLASSE==='1').reduce((s,r)=>s+r.SALDO,0),p=fil.filter(r=>r.CLASSE==='2').reduce((s,r)=>s+r.SALDO,0);
  tf.innerHTML=`<tr><td colspan="3">Ativo: ${brl(a)} &nbsp;·&nbsp; Passivo: ${brl(p)} &nbsp;(${fil.length.toLocaleString('pt-BR')} registros — Ativo e Passivo não se somam)</td><td></td></tr>`;
}
function tog(id){expandidos.has(id)?[...expandidos].filter(x=>x===id||x.startsWith(id+'|')).forEach(x=>expandidos.delete(x)):expandidos.add(id);render();}
function expandirContas(){fil.forEach(r=>expandidos.add('u'+r.UGKEY));render();}
function recolherTudo(){expandidos.clear();render();}

// ── KPIs ──
function kpis(){
  if(!fil.length){document.getElementById('krow').innerHTML='';return;}
  const at=fil.filter(r=>r.CLASSE==='1'),pa=fil.filter(r=>r.CLASSE==='2');
  const sa=at.reduce((s,r)=>s+r.SALDO,0),sp=pa.reduce((s,r)=>s+r.SALDO,0);
  const ugs=new Set(fil.map(r=>r.COUG)).size,contas=new Set(fil.map(r=>r.CONTA_CONTABIL)).size;
  const min=Math.min(...fil.map(r=>r.ANO));
  // % sobre o total atual: numerador = antigos; denominador = todos os saldos (sem filtros de ano/tipo/origem)
  const spx=pa.filter(r=>r.CONTA_CONTABIL.startsWith('21')||r.CONTA_CONTABIL.startsWith('22')).reduce((s,r)=>s+r.SALDO,0);
  const cl=document.getElementById('f-classe').value;
  const den={a:0,p:0};
  TOT.forEach(t=>{
    if(acState.g.sel&&t.g!==acState.g.sel)return;
    if(acState.u.sel&&t.u!==acState.u.sel)return;
    if(acState.c.sel&&t.c!==acState.c.sel)return;
    if(t.c[0]==='1'){if(!cl||cl==='1')den.a+=t.s;}
    else if(!cl||cl==='2')den.p+=t.s;
  });
  const pct=(x,d)=>d>0?(x/d*100).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})+'%':'—';
  const inf=fil.filter(r=>r.ORIGEM==='Ano inferido do nº do processo').length;
  document.getElementById('krow').innerHTML=`
    <div class="kpi"><div class="kl">Registros</div><div class="kv">${fil.length.toLocaleString('pt-BR')}</div><div class="ks">${ugs} UG${ugs!==1?'s':''} · ${contas} conta${contas!==1?'s':''}${inf?` · ${inf.toLocaleString('pt-BR')} com ano inferido do processo`:''}</div></div>
    <div class="kpi"><div class="kl">Ativo (classe 1)</div><div class="kv">${brl(sa)}</div><div class="ks">${at.length.toLocaleString('pt-BR')} registros</div></div>
    <div class="kpi kw"><div class="kl">Passivo (classe 2)</div><div class="kv">${brl(sp)}</div><div class="ks">${pa.length.toLocaleString('pt-BR')} registros</div></div>
    <div class="kpi"><div class="kl">% do Ativo atual</div><div class="kv">${pct(sa,den.a)}</div><div class="ks">${brl(sa)} de ${brl(den.a)} (Ativo total)</div></div>
    <div class="kpi kw"><div class="kl">% do Passivo atual</div><div class="kv">${pct(spx,den.p)}</div><div class="ks">${brl(spx)} de ${brl(den.p)} (Passivo exigível 2.1 + 2.2)</div></div>
    <div class="kpi ka"><div class="kl">Ano mais antigo</div><div class="kv">${min}</div><div class="ks">${META.ano_exercicio-min} anos</div></div>`;
}

// ── gráfico por ano ──
function grafico(){
  const m={};
  fil.forEach(r=>{const o=m[r.ANO]||(m[r.ANO]={a:0,p:0});r.CLASSE==='1'?o.a+=Math.abs(r.SALDO):o.p+=Math.abs(r.SALDO);});
  const anos=Object.keys(m).map(Number).sort((a,b)=>a-b);
  const max=Math.max(1,...anos.map(a=>Math.max(m[a].a,m[a].p)));
  const sel=document.getElementById('f-ano').value;
  document.getElementById('bars').innerHTML=anos.map(a=>{
    const ha=Math.round(Math.sqrt(m[a].a/max)*115),hp=Math.round(Math.sqrt(m[a].p/max)*115);
    return `<div class="bcol ${String(a)===sel?'sel':''}" onclick="filtrarAno(${a})" title="${a} · Ativo ${brl(m[a].a)} · Passivo ${brl(m[a].p)} (valores absolutos)">
      <div class="bpair"><div class="b b1" style="height:${ha}px"></div><div class="b b2" style="height:${hp}px"></div></div><div class="by">${a}</div></div>`;}).join('');
}
function filtrarAno(a){const s=document.getElementById('f-ano');s.value=String(s.value)===String(a)?'':String(a);aplicar();}

// ── critérios de seleção (dropdown ℹ️) ──
(function(){
  const s=META.sem_ano,n=x=>x.toLocaleString('pt-BR');
  document.getElementById('crit-lista').innerHTML=[
   `<strong>Contas consideradas:</strong> classe 1 (Ativo) e classe 2 (Passivo e Patrimônio Líquido) com saldo diferente de zero, somando todos os meses do exercício ${META.ano_exercicio}.`,
   `<strong>Critério de antiguidade:</strong> ano ≤ ${META.ano_corte} (exercício ${META.ano_exercicio} − ${META.anos_limite} anos), lido do próprio <code>COCONTACORRENTE</code> conforme o tipo (VCONTACONTABIL.INCONTACORRENTE). Só entram tipos de conta corrente que trazem ano.`,
   `<strong>Onde está o ano:</strong> tipo 04 (Ano + CGC/CPF/IG/UG-Gestão): início; tipos 63, 76 e demais com NE na frente, e tipo 06 (Ordem Bancária): ano do documento (AAAANE…/AAAAOB…); tipo 67 (Fonte + Ano + Credor): após a fonte; tipo 00 (Nulo): só quando o conteúdo tem padrão de NE.`,
   `<strong>Ano inferido:</strong> no tipo 45 (Nº Processo + CGC/CPF/…) o ano é o do número do processo, não necessariamente o da obrigação. Use o filtro "Origem do ano" para separar.`,
   `<strong>Saldo:</strong> classe 1 = débito − crédito; classe 2 = crédito − débito (negativo = saldo invertido). Ativo e Passivo não se somam.`,
   `<strong>Leitura:</strong> Ativo = possíveis créditos antigos a avaliar (recebimento, baixa, ajuste para perdas); Passivo = obrigações exigíveis há mais de ${META.anos_limite} anos a avaliar (pagamento, cancelamento, possível prescrição).`,
   `<strong>% do Ativo / do Passivo atual:</strong> saldo antigo ÷ saldo total de todas as contas de classe 1 (Ativo) e das contas 2.1 e 2.2 (Passivo exigível; o Patrimônio Líquido, 2.3, fica fora do percentual), considerando os mesmos filtros de classe, gestão, UG e conta — sem os filtros de ano, tipo e origem.`,
   `<strong>Fora do painel:</strong> ${n(s.n)} registros com saldo, de tipos que trazem ano, mas sem ano identificável (formato antigo, ex.: só CNPJ/CPF), somando ${brl(s.abs)} em módulo; e ${n(META.recentes.n)} registros com ano posterior a ${META.ano_corte}.`,
   `<strong>Proteção de dados pessoais (LGPD):</strong> documentos de 11 dígitos (CPF; podem coincidir com UG+Gestão) foram substituídos por um pseudônimo ${META.mascara_formato} — HMAC-SHA256 com chave secreta mantida fora do código, da página e do repositório. O mesmo documento gera sempre o mesmo pseudônimo, o que permite agrupar sem identificar. CNPJ (14 dígitos) não é mascarado. O CSV exportado já sai pseudonimizado.`
  ].map(t=>`<li>${t}</li>`).join('');
})();

// ── CSV ──
function exportar(){
  if(!fil.length)return alert('Nenhum dado para exportar.');
  const cols=['CLASSE','CONTA_CONTABIL','NOCONTACONTABIL','COGESTAO','NOGESTAO','COUG','NOUG','TIPO','CONTA_CORRENTE','ANO','ORIGEM','SALDO'];
  const hdr=['Classe','Conta Contabil','Nome Conta','Gestao','Nome Gestao','UG','Nome UG','Tipo CC','Conta Corrente','Ano','Origem do Ano','Saldo'];
  const cel=v=>{if(typeof v==='number')return String(v).replace('.',',');const s=String(v??'');return /^\d{9,}$/.test(s)?`="${s}"`:s.replace(/;/g,',');};
  const linhas=[hdr.join(';'),...fil.map(r=>cols.map(c=>cel(r[c])).join(';'))];
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob(['\ufeff'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),download:'saldos_antigos_cc_ano.csv'});
  a.click();URL.revokeObjectURL(a.href);
}
Object.assign(window,{onACInput,onACFocus,onACBlur,limparAC,aplicar,limpar,tog,expandirContas,recolherTudo,exportar,filtrarAno});
aplicar();
</script>
</body>
</html>"""


# ── Oracle ──────────────────────────────────────────────────────────────────────
oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")


def consultar(cur, sql: str) -> pd.DataFrame:
    cur.execute(sql)
    return pd.DataFrame(cur.fetchall(), columns=[c[0] for c in cur.description])


def extrair(ano_corte: int):
    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle…")
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        with conn.cursor() as cur:
            print(f"[{datetime.now():%H:%M:%S}] Extraindo saldos das classes 1 e 2 (pode levar alguns minutos)…")
            base = consultar(cur, SQL_SALDOS)
            ugs = consultar(cur, SQL_UGS)
            gestoes = consultar(cur, SQL_GESTOES)
            contas = consultar(cur, SQL_CONTAS)
            mes = int(consultar(cur, SQL_MES)["MES"].iloc[0])
    print(f"[{datetime.now():%H:%M:%S}] {len(base):,} saldos diferentes de 0 nas classes 1 e 2.")

    base["TIPO"] = base["TIPO"].fillna("00").astype(str).str.strip().str.zfill(2)
    base["CONTA_CONTABIL"] = base["CONTA_CONTABIL"].astype(str)
    base["COUG"] = base["COUG"].astype(str)
    base["COGESTAO"] = base["COGESTAO"].astype(str).str.zfill(5)
    base["CONTA_CORRENTE"] = base["CONTA_CORRENTE"].fillna("").astype(str).str.rstrip()
    base["CLASSE"] = base["CONTA_CONTABIL"].str[0]
    deb = pd.to_numeric(base["DEBITO"], errors="coerce").fillna(0)
    cred = pd.to_numeric(base["CREDITO"], errors="coerce").fillna(0)
    base["SALDO"] = (deb - cred).where(base["CLASSE"] == "1", cred - deb).round(2)

    # ano da conta corrente
    res = [extrair_ano(t, cc) for t, cc in zip(base["TIPO"], base["CONTA_CORRENTE"])]
    base["ANO"] = [r[0] for r in res]
    base["ORIGEM"] = [r[1] for r in res]

    # alerta: tipos do catálogo com ano e sem regra, que têm saldo
    for t, desc in TIPOS_COM_ANO_SEM_REGRA.items():
        n = int((base["TIPO"] == t).sum())
        if n:
            print(f"  ATENCAO: {n:,} saldos de tipo {t} ({desc}) — layout traz ano mas a regra ainda NÃO foi implementada.")

    # fora do painel: tipos que trazem ano (explícito) mas sem ano identificável
    tipos_com_ano = {"04", "12", "67", "06", "45"} | TIPOS_NE_NA_FRENTE
    sem_ano = base[base["ANO"].isna() & base["TIPO"].isin(tipos_com_ano)]
    recentes = base[base["ANO"].notna() & (base["ANO"] > ano_corte)]
    antigos = base[base["ANO"].notna() & (base["ANO"] <= ano_corte)].copy()
    antigos["ANO"] = antigos["ANO"].astype(int)

    antigos = (antigos
               .merge(ugs.rename(columns={"COUG": "COUG", "NOUG": "NOUG"}).astype({"COUG": str}), on="COUG", how="left")
               .merge(gestoes.astype({"COGESTAO": str}).assign(COGESTAO=lambda d: d["COGESTAO"].str.zfill(5)), on="COGESTAO", how="left")
               .merge(contas.rename(columns={"COCONTACONTABIL": "CONTA_CONTABIL"}).astype({"CONTA_CONTABIL": str}), on="CONTA_CONTABIL", how="left"))
    for c in ("NOUG", "NOGESTAO", "NOCONTACONTABIL"):
        antigos[c] = antigos[c].fillna("")
    antigos = antigos.sort_values(["CONTA_CONTABIL", "COUG", "ANO", "CONTA_CORRENTE"]).reset_index(drop=True)

    # proteção de dados pessoais: pseudonimiza documentos de 11 dígitos ANTES de qualquer saída
    chave = carregar_chave_anonimizacao()
    antigos["CONTA_CORRENTE"] = [pseudonimizar_cc(chave, t, cc) for t, cc in zip(antigos["TIPO"], antigos["CONTA_CORRENTE"])]
    n_pseudo = verificar_sem_documentos(antigos["CONTA_CORRENTE"])
    print(f"  {n_pseudo:,} contas correntes tiveram documento de 11 digitos pseudonimizado (verificado: nenhum em claro).")

    # denominador dos percentuais: saldo atual total de Ativo (classe 1) e Passivo exigível (2.1 e 2.2)
    atual = base[base["CONTA_CONTABIL"].str[:1].eq("1") | base["CONTA_CONTABIL"].str[:2].isin(["21", "22"])]
    totais = (atual.groupby(["COGESTAO", "COUG", "CONTA_CONTABIL"], as_index=False)["SALDO"].sum().round(2)
              .rename(columns={"COGESTAO": "g", "COUG": "u", "CONTA_CONTABIL": "c", "SALDO": "s"}))

    meta = {
        "mascara_formato": MASCARA_FORMATO,
        "ano_exercicio": ANO_EXERCICIO, "anos_limite": ANOS_LIMITE, "ano_corte": ano_corte,
        "tipos": {t: TIPO_ROTULO.get(t, t) for t in sorted(antigos["TIPO"].unique())},
        "sem_ano": {"n": int(len(sem_ano)), "abs": round(float(sem_ano["SALDO"].abs().sum()), 2)},
        "recentes": {"n": int(len(recentes))},
        "mes_ref": mes,
    }
    return antigos, meta, totais


COLUNAS_JSON = ["CLASSE", "CONTA_CONTABIL", "NOCONTACONTABIL", "COGESTAO", "NOGESTAO", "COUG", "NOUG",
                "TIPO", "CONTA_CORRENTE", "ANO", "ORIGEM", "SALDO"]


def gerar_html(df: pd.DataFrame, meta: dict, totais: pd.DataFrame) -> str:
    registros = df[COLUNAS_JSON].to_dict(orient="records")
    for r in registros:
        r["ANO"] = int(r["ANO"])
        r["SALDO"] = float(r["SALDO"])
    ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    html = HTML_TEMPLATE
    for chave, valor in {
        "__DADOS__": json.dumps(registros, ensure_ascii=False, separators=(",", ":")),
        "__TOTAIS__": json.dumps(totais.to_dict(orient="records"), ensure_ascii=False, separators=(",", ":")),
        "__META__": json.dumps(meta, ensure_ascii=False),
        "__TIMESTAMP__": ts,
        "__ANO_CORTE__": str(meta["ano_corte"]),
        "__ANOS_LIMITE__": str(meta["anos_limite"]),
    }.items():
        html = html.replace(chave, valor)
    return html


def publicar_github(caminho: str, mensagem_commit: str) -> None:
    if os.environ.get("NO_GIT_PUSH"):
        return
    pasta = Path(__file__).parent
    url_remote = f"https://{os.environ['GITHUB_TOKEN']}@github.com/{os.environ['GITHUB_USER']}/{os.environ['GITHUB_REPO']}.git"
    branch = os.environ["GITHUB_BRANCH"]

    def git(*args):
        r = subprocess.run(["git", "-C", str(pasta)] + list(args), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return r.stdout.strip()

    print(f"[{datetime.now():%H:%M:%S}] Publicando no GitHub…")
    try:
        git("remote", "set-url", "origin", url_remote)
    except RuntimeError:
        git("remote", "add", "origin", url_remote)
    git("add", caminho)
    git("commit", "-m", mensagem_commit)
    git("pull", "--rebase", "--autostash", "origin", branch)
    git("push", "origin", branch)
    print(f"  -> https://{os.environ['GITHUB_USER']}.github.io/{os.environ['GITHUB_REPO']}/{caminho}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Saldos antigos por ano da conta corrente (classes 1 e 2).")
    parser.add_argument("--ano-corte", type=int, default=ANO_CORTE, help=f"Ano máximo a listar (padrão {ANO_CORTE})")
    parser.add_argument("--out", type=str, default=ARQUIVO_HTML)
    parser.add_argument("--push", action="store_true", help="Publica no GitHub Pages (por padrão só gera o HTML local)")
    args = parser.parse_args()

    try:
        df, meta, totais = extrair(args.ano_corte)
    except oracledb.DatabaseError as e:
        print(f"\nErro de banco de dados: {e}", file=sys.stderr)
        sys.exit(1)

    html = gerar_html(df, meta, totais)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"[{datetime.now():%H:%M:%S}] HTML salvo: {args.out} ({len(html)/1e6:.1f} MB)")

    if args.push:
        publicar_github(ARQUIVO_HTML, f"chore: atualiza saldos antigos por ano da conta corrente — {datetime.now():%d/%m/%Y %H:%M}")

    def brl(v: float) -> str:
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    print("\n-- Resumo ----------------------------------------------------------")
    print(f"  Ano de corte           : <= {args.ano_corte}")
    print(f"  Registros no painel    : {len(df):,}")
    for cl, nome in (("1", "Ativo"), ("2", "Passivo")):
        g = df[df["CLASSE"] == cl]
        print(f"  {nome:<7}: {len(g):>7,} registros | saldo {brl(float(g['SALDO'].sum()))}")
    print("  Por tipo de conta corrente:")
    for t, g in df.groupby("TIPO"):
        print(f"    tipo {t}: {len(g):>7,} registros | {g['CONTA_CONTABIL'].nunique():>3} contas | {brl(float(g['SALDO'].abs().sum()))} em módulo")
    print(f"  Sem ano identificável  : {meta['sem_ano']['n']:,} (tipos que trazem ano, formato antigo)")
    print(f"  Ano > corte (fora)     : {meta['recentes']['n']:,}")
    print("-------------------------------------------------------------------\n")


if __name__ == "__main__":
    main()
