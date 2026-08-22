"""
Correspondência Patrimonial Ativo x Passivo — por Lançamento
Gera painel HTML autocontido e publica no GitHub Pages.

Monitora equações entre contas do Ativo (classe 1) e do Passivo (classe 2),
consultando VLANCAMENTOCONTABIL. Um lançamento cria um Ativo em uma UG e um
Passivo em outra; o painel agrupa por documento + UG Emitente para rastreamento.

Equações monitoradas (extensíveis em EQUACOES):
  EQ1: 21142XXXX = 113620101 + 113620103
  EQ2: 218820101 = 113620102 + 113620104
  EQ3: 218820104 = 112120101
  EQ4: 218820107 = 112120104
  EQ5: 214320100 + 214325100 + 218820108 + 218827005 = 112120107
  EQ6: 218924019 = 112322200

Natureza das contas:
  Classe 1 (Ativo)  — devedora: VLNET = D - C
  Classe 2 (Passivo) — credora: VLNET = C - D

Uso:
    python extrair_correspondencia_ativo_passivo.py
    python extrair_correspondencia_ativo_passivo.py --no-push
"""
import argparse, base64, gzip, json, os, subprocess, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from datetime import date, datetime
from pathlib import Path

import oracledb
import pandas as pd
from dotenv import load_dotenv

PASTA        = Path(__file__).parent
load_dotenv(PASTA / ".env")

ORACLE_USER   = os.environ["ORACLE_USER"]
ORACLE_PASS   = os.environ["ORACLE_PASS"]
ORACLE_DSN    = os.environ["ORACLE_DSN"]
SCHEMA        = "MIL2026."
ARQUIVO_HTML  = "correspondencia_ativo_passivo.html"

INSTANT_CLIENT_DIR = r"C:\oracle\instantclient_23_0"

MESES = {1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
         7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}

# Equações monitoradas — adicione mais linhas para expandir.
# passivo_prefix: 5 primeiros dígitos para contas do tipo 21142XXXX (intervalo).
# passivo_exact:  lista de contas exatas quando não há intervalo.
EQUACOES = [
    {"id": "EQ1", "desc": "21142XXXX = 113620101 + 113620103",
     "passivo_exact": [], "passivo_prefix": "21142",
     "ativo": [113620101, 113620103]},
    {"id": "EQ2", "desc": "218820101 = 113620102 + 113620104",
     "passivo_exact": [218820101], "passivo_prefix": None,
     "ativo": [113620102, 113620104]},
    {"id": "EQ3", "desc": "218820104 = 112120101",
     "passivo_exact": [218820104], "passivo_prefix": None,
     "ativo": [112120101]},
    {"id": "EQ4", "desc": "218820107 = 112120104",
     "passivo_exact": [218820107], "passivo_prefix": None,
     "ativo": [112120104]},
    {"id": "EQ5", "desc": "214320100 + 214325100 + 218820108 + 218827005 = 112120107",
     "passivo_exact": [214320100, 214325100, 218820108, 218827005], "passivo_prefix": None,
     "ativo": [112120107]},
    {"id": "EQ6", "desc": "218924019 = 112322200",
     "passivo_exact": [218924019], "passivo_prefix": None,
     "ativo": [112322200]},
    {"id": "EQ7", "desc": "113220700 + 112920101 = 0",
     "passivo_exact": [], "passivo_prefix": None,
     "ativo": [113220700, 112920101]},
]

_CONTAS_ATIVO  = sorted({c for eq in EQUACOES for c in eq["ativo"]})
_CONTAS_EXATAS = sorted({c for eq in EQUACOES for c in eq["passivo_exact"]})
_PREFIXOS      = [eq["passivo_prefix"] for eq in EQUACOES if eq["passivo_prefix"]]

def _where_contas() -> str:
    parts = []
    if _CONTAS_ATIVO + _CONTAS_EXATAS:
        lst = ",".join(str(c) for c in sorted(_CONTAS_ATIVO + _CONTAS_EXATAS))
        parts.append(f"v.COCONTACONTABIL IN ({lst})")
    for pfx in _PREFIXOS:
        lo, hi = int(pfx) * 10**(9 - len(pfx)), (int(pfx) + 1) * 10**(9 - len(pfx)) - 1
        parts.append(f"v.COCONTACONTABIL BETWEEN {lo} AND {hi}")
    return " OR ".join(parts)

# ── SQL ───────────────────────────────────────────────────────────────────────
# Busca todos os meses do exercício; filtro de mês aplicado no cliente.
# VLNET usa sinal correto por classe:
#   Classe 1 (ativo/devedora):  D - C
#   Classe 2 (passivo/credora): C - D
SQL = f"""
SELECT
  v.INMES,
  v.COGESTAO                                      AS COGESTAO_EMIT,
  v.COUG                                          AS COUG_EMIT,
  v.COGESTAOCONTAB                                AS COGESTAO,
  v.COUGCONTAB                                    AS COUG,
  TRIM(v.NUDOCUMENTO)                             AS NUDOCUMENTO,
  TO_CHAR(v.COCONTACONTABIL)                      AS COCONTACONTABIL,
  v.COEVENTO,
  TO_CHAR(MIN(v.DALANCAMENTO), 'DD/MM/YYYY')      AS DATA,
  TO_CHAR(MIN(v.DALANCAMENTO), 'YYYY/MM/DD')      AS DATA_ISO,
  ROUND(SUM(
    CASE SUBSTR(TO_CHAR(v.COCONTACONTABIL), 1, 1)
      WHEN '1' THEN CASE v.INDEBITOCREDITO WHEN 'D' THEN  v.VALANCAMENTO
                                           ELSE            -v.VALANCAMENTO END
      WHEN '2' THEN CASE v.INDEBITOCREDITO WHEN 'C' THEN  v.VALANCAMENTO
                                           ELSE            -v.VALANCAMENTO END
      ELSE 0
    END
  ), 2) AS VLNET
FROM {SCHEMA}VLANCAMENTOCONTABIL v
WHERE ({_where_contas()})
GROUP BY
  v.INMES, v.COGESTAO, v.COUG,
  v.COGESTAOCONTAB, v.COUGCONTAB,
  TRIM(v.NUDOCUMENTO), TO_CHAR(v.COCONTACONTABIL),
  v.COEVENTO
ORDER BY MIN(v.DALANCAMENTO), v.COUG, TRIM(v.NUDOCUMENTO), TO_CHAR(v.COCONTACONTABIL)
"""

SQL_UG = f"""
SELECT TO_CHAR(COUG) AS COUG, TRIM(NOUG) AS NOUG
FROM {SCHEMA}VUNIDADEGESTORA
"""

SQL_GESTAO = f"""
SELECT TO_CHAR(COGESTAO) AS COGESTAO, TRIM(NOGESTAO) AS NOGESTAO
FROM {SCHEMA}GESTAO
"""

SQL_CONTA = f"""
SELECT TO_CHAR(COCONTACONTABIL) AS COCONTACONTABIL, TRIM(NOCONTACONTABIL) AS NOCONTACONTABIL
FROM {SCHEMA}VCONTACONTABIL
WHERE COCONTACONTABIL IN ({",".join(str(c) for c in sorted(_CONTAS_ATIVO + _CONTAS_EXATAS))})
"""

# ── HTML Template ─────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<!-- v-corresp-ativo-passivo-2 -->
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Correspondência Ativo x Passivo — SIGGO</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --navy:#0d1b3e;--navy-mid:#162550;--navy-light:#1e3267;
  --teal:#0090a8;--teal-light:#00b8d4;
  --surface:#fff;--bg:#f2f5f9;--border:#dce3ed;
  --row-alt:#f4f7fb;--hover:#e8f0f8;
  --text:#1a2033;--muted:#6b7a99;
  --red:#c0392b;--amber:#b7860b;--green:#1a7a44;--radius:10px;
  --shadow:0 2px 12px rgba(13,27,62,.10);
}
body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}
header{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}
.hlogo{width:32px;height:32px;background:var(--teal);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}
header h1{font-size:14px;font-weight:700;letter-spacing:.6px;text-transform:uppercase}
header h1 span{font-weight:400;color:#9ab0cc;font-size:12px;display:block;text-transform:none;letter-spacing:0;margin-top:1px}
.voltar{font-size:11px;color:#7a99bb;text-decoration:none;display:flex;align-items:center;gap:4px;margin-left:20px;opacity:.8}
.voltar:hover{opacity:1}
#ts{font-size:11px;color:#7a99bb;white-space:nowrap}
.fbar{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
.fg{display:flex;flex-direction:column;gap:4px}
.fg label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.fg select{border:1.5px solid var(--border);border-radius:6px;padding:7px 28px 7px 10px;font-size:12.5px;min-width:130px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E") no-repeat right 9px center;color:var(--text);cursor:pointer;appearance:none}
.fg select:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-wrap{position:relative}
.ac-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text)}
.ac-input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}
.ac-dd{position:absolute;top:calc(100% + 4px);left:0;right:0;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:240px;overflow-y:auto;display:none}
.ac-dd-item{padding:8px 12px;cursor:pointer;font-size:12px;border-bottom:1px solid var(--border)}
.ac-dd-item:last-child{border-bottom:none}
.ac-dd-item:hover{background:var(--hover)}
.ac-dd-item strong{color:var(--navy);font-weight:700}
.ac-dd-empty{padding:12px;color:var(--muted);font-size:12px;text-align:center}
.bgrp{display:flex;gap:8px;margin-left:auto;align-items:flex-end;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;transition:filter .15s,transform .1s;white-space:nowrap}
.btn:hover{filter:brightness(1.08);transform:translateY(-1px)}
.btn-p{background:var(--teal);color:#fff}
.btn-g{background:var(--border);color:var(--text)}
.btn-eq1{background:#fde8e6;color:var(--red);border:1.5px solid #f5c0ba}
.btn-eq1.active,.btn-eq1:hover{background:var(--red);color:#fff;border-color:var(--red)}
.btn-eq2{background:#fef9e2;color:var(--amber);border:1.5px solid #f0dc8a}
.btn-eq2.active,.btn-eq2:hover{background:#c9950a;color:#fff;border-color:#c9950a}
.btn-eq3{background:#e8f5e9;color:#2e7d32;border:1.5px solid #a5d6a7}
.btn-eq3.active,.btn-eq3:hover{background:#2e7d32;color:#fff;border-color:#2e7d32}
.btn-eq4{background:#ede7f6;color:#5e35b1;border:1.5px solid #ce93d8}
.btn-eq4.active,.btn-eq4:hover{background:#5e35b1;color:#fff;border-color:#5e35b1}
.btn-eq5{background:#fff3e0;color:#e65100;border:1.5px solid #ffcc80}
.btn-eq5.active,.btn-eq5:hover{background:#e65100;color:#fff;border-color:#e65100}
.btn-eq6{background:#e0f7fa;color:#00695c;border:1.5px solid #80cbc4}
.btn-eq6.active,.btn-eq6:hover{background:#00695c;color:#fff;border-color:#00695c}
.btn-eq7{background:#fce4ec;color:#880e4f;border:1.5px solid #f48fb1}
.btn-eq7.active,.btn-eq7:hover{background:#880e4f;color:#fff;border-color:#880e4f}
.emit-wrap{position:relative;min-width:180px}
.emit-in{border:2px solid var(--teal);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;box-shadow:0 0 0 2px rgba(0,144,168,.08)}
.emit-in:focus{outline:none;border-color:var(--navy)}
.emit-clr{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}
.emit-dd{position:absolute;top:calc(100% + 4px);left:0;min-width:200px;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:300;max-height:220px;overflow-y:auto;display:none}
.krow{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;padding:18px 28px 4px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}
.kpi.ka::before{background:linear-gradient(90deg,var(--red),#e74c3c)}
.kpi.km::before{background:linear-gradient(90deg,var(--amber),#e6b800)}
.kpi.kg::before{background:linear-gradient(90deg,#2e7d32,#43a047)}
.kpi.kv4::before{background:linear-gradient(90deg,#5e35b1,#7e57c2)}
.kpi.kv5::before{background:linear-gradient(90deg,#e65100,#fb8c00)}
.kpi.kv6::before{background:linear-gradient(90deg,#00695c,#00897b)}
.kpi.kv7::before{background:linear-gradient(90deg,#880e4f,#c2185b)}
.kl{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.kv{font-size:19px;font-weight:700;letter-spacing:-.3px;line-height:1}
.ks{font-size:11px;color:var(--muted);margin-top:5px}
.tsec{padding:16px 28px 32px}
.thead-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.ttitle{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.tw{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:1100px;table-layout:fixed}
.sw{position:relative}
.sw input{border:2px solid var(--teal);border-radius:6px;padding:7px 12px 7px 32px;font-size:12.5px;min-width:260px;background:#fff;box-shadow:0 0 0 2px rgba(0,144,168,.08)}
.sw input:focus{outline:none;border-color:var(--navy)}
.sw::before{content:'&#128269;';position:absolute;left:9px;top:50%;transform:translateY(-50%);font-size:12px;pointer-events:none}
thead th{background:var(--navy);color:#c8d8ec;padding:10px 12px;font-size:11px;font-weight:600;text-align:right;white-space:nowrap;letter-spacing:.3px;cursor:pointer;user-select:none;overflow:hidden;text-overflow:ellipsis}
thead th.nosort{cursor:default}
thead th.left{text-align:left}
thead th:hover:not(.nosort){background:#2a4a7a}
.si{display:inline-block;width:13px;text-align:center;opacity:.6;font-size:10px}
tr.row-doc{background:#1e3267;cursor:pointer}
tr.row-doc td{color:#e8f0fc;font-weight:700;padding:9px 12px;font-size:12px;white-space:nowrap;text-align:right}
tr.row-doc td.left{text-align:left}
tr.row-doc:hover td{background:#2a4580}
tr.dr{background:var(--surface);display:none}
tr.dr.alt{background:var(--row-alt)}
tr.dr td{padding:7px 12px;border-bottom:1px solid var(--border);font-size:12px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis}
tr.dr td.left{text-align:left}
tr.dr:hover td{background:var(--hover)}
tfoot tr td{background:#e8f0f8;font-weight:700;border-top:2px solid var(--teal);padding:10px 12px;font-size:12px;text-align:right}
tfoot tr td.left{text-align:left}
.tog{display:inline-block;width:14px;text-align:center;font-style:normal;margin-right:4px}
.vp{color:var(--green);font-weight:600}
.vn{color:var(--red);font-weight:600}
.vz{color:var(--muted)}
.pgbar{display:flex;align-items:center;justify-content:center;gap:6px;padding:12px;font-size:12px;color:var(--muted)}
.pgbar button{border:1px solid var(--border);border-radius:5px;padding:4px 10px;background:#fff;cursor:pointer;font-size:12px}
.pgbar button:hover{background:var(--hover)}
.pgbar button:disabled{opacity:.4;cursor:default}
.pgbar .pgcur{background:var(--teal);color:#fff;border-color:var(--teal)}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">&#9878;</div>
    <h1>Correspond&#234;ncia Patrimonial &#8212; Ativo &#215; Passivo
      <span>SIGGO &#183; La&#231;amentos &#183; Ano Exerc&#237;cio 2026</span>
    </h1>
    <a class="voltar" href="index.html">&#8592; Painel inicial</a>
  </div>
  <span id="ts">Atualizado em __TIMESTAMP__</span>
</header>

<div class="fbar">
  <div class="fg">
    <label>Gest&#227;o</label>
    <div class="ac-wrap" style="min-width:130px">
      <input id="fg-input" class="ac-input" type="text" placeholder="C&#243;digo&#8230;" autocomplete="off"
             oninput="onAC('g')" onfocus="onAC('g')" onblur="blurAC('g')">
      <button class="ac-clear" id="fg-clear" onclick="limparAC('g')">&#x2715;</button>
      <div class="ac-dd" id="fg-dd"></div>
    </div>
  </div>
  <div class="fg">
    <label>Unidade Gestora</label>
    <div class="ac-wrap" style="min-width:230px">
      <input id="fu-input" class="ac-input" type="text" placeholder="C&#243;digo ou nome&#8230;" autocomplete="off"
             oninput="onAC('u')" onfocus="onAC('u')" onblur="blurAC('u')">
      <button class="ac-clear" id="fu-clear" onclick="limparAC('u')">&#x2715;</button>
      <div class="ac-dd" id="fu-dd"></div>
    </div>
  </div>
  <div class="fg">
    <label>M&#234;s</label>
    <select id="fm"><option value="">Todos</option></select>
  </div>
  <div class="fg">
    <label>Equival&#234;ncia</label>
    <select id="feq"><option value="">Todas</option></select>
  </div>
  <div class="bgrp">
    <button class="btn btn-eq1" id="btn-eq1" onclick="filtrarEq('eq1')">&#9888; Diverg. EQ1</button>
    <button class="btn btn-eq2" id="btn-eq2" onclick="filtrarEq('eq2')">&#9888; Diverg. EQ2</button>
    <button class="btn btn-eq3" id="btn-eq3" onclick="filtrarEq('eq3')">&#9888; Diverg. EQ3</button>
    <button class="btn btn-eq4" id="btn-eq4" onclick="filtrarEq('eq4')">&#9888; Diverg. EQ4</button>
    <button class="btn btn-eq5" id="btn-eq5" onclick="filtrarEq('eq5')">&#9888; Diverg. EQ5</button>
    <button class="btn btn-eq6" id="btn-eq6" onclick="filtrarEq('eq6')">&#9888; Diverg. EQ6</button>
    <button class="btn btn-eq7" id="btn-eq7" onclick="filtrarEq('eq7')">&#9888; Diverg. EQ7</button>
    <button class="btn btn-g" onclick="limpar()">&#8635; Limpar filtros</button>
    <button class="btn btn-p" onclick="exportarCSV()">&#8615; Exportar CSV</button>
  </div>
</div>

<div class="krow">
  <div class="kpi" id="kpi-ativo">
    <div class="kl">Ativo (Classe 1)</div>
    <div class="kv" id="kv-ativo">&#8212;</div>
    <div class="ks" id="ks-ativo"></div>
  </div>
  <div class="kpi" id="kpi-passivo">
    <div class="kl">Passivo (Classe 2)</div>
    <div class="kv" id="kv-passivo">&#8212;</div>
    <div class="ks" id="ks-passivo"></div>
  </div>
  <div class="kpi" id="kpi-eq1">
    <div class="kl">Diverg&#234;ncia EQ1</div>
    <div class="kv" id="kv-eq1">&#8212;</div>
    <div class="ks" id="ks-eq1"></div>
  </div>
  <div class="kpi" id="kpi-eq2">
    <div class="kl">Diverg&#234;ncia EQ2</div>
    <div class="kv" id="kv-eq2">&#8212;</div>
    <div class="ks" id="ks-eq2"></div>
  </div>
  <div class="kpi" id="kpi-eq3">
    <div class="kl">Diverg&#234;ncia EQ3</div>
    <div class="kv" id="kv-eq3">&#8212;</div>
    <div class="ks" id="ks-eq3"></div>
  </div>
  <div class="kpi" id="kpi-eq4">
    <div class="kl">Diverg&#234;ncia EQ4</div>
    <div class="kv" id="kv-eq4">&#8212;</div>
    <div class="ks" id="ks-eq4"></div>
  </div>
  <div class="kpi" id="kpi-eq5">
    <div class="kl">Diverg&#234;ncia EQ5</div>
    <div class="kv" id="kv-eq5">&#8212;</div>
    <div class="ks" id="ks-eq5"></div>
  </div>
  <div class="kpi" id="kpi-eq6">
    <div class="kl">Diverg&#234;ncia EQ6</div>
    <div class="kv" id="kv-eq6">&#8212;</div>
    <div class="ks" id="ks-eq6"></div>
  </div>
  <div class="kpi" id="kpi-eq7">
    <div class="kl">Diverg&#234;ncia EQ7</div>
    <div class="kv" id="kv-eq7">&#8212;</div>
    <div class="ks" id="ks-eq7"></div>
  </div>
</div>

<div class="tsec">
  <div class="thead-row">
    <span class="ttitle" id="ttitle">Documentos</span>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <div class="emit-wrap">
        <input id="emit-in" class="emit-in" type="text" placeholder="Gest&#227;o-UG Emitente&#8230;"
               autocomplete="off" oninput="onEmit()" onfocus="onEmit()" onblur="blurEmit()">
        <button class="emit-clr" id="emit-clr" onclick="limparEmit()">&#x2715;</button>
        <div class="emit-dd" id="emit-dd"></div>
      </div>
      <div class="sw"><input id="busca" type="text" placeholder="Buscar por n&#186; documento ou evento&#8230;"></div>
      <div id="pgbar-top" class="pgbar"></div>
    </div>
  </div>
  <div class="tw">
    <table>
      <thead><tr>
        <th class="nosort left" style="width:36px"></th>
        <th class="left" style="width:195px" onclick="sortBy('UG')">Gest&#227;o &#183; UG <span id="s_UG" class="si">&#8645;</span></th>
        <th class="left nosort" style="width:115px">Conta Cont&#225;bil</th>
        <th style="width:120px" onclick="sortBy('ATIVO')">Ativo <span id="s_ATIVO" class="si">&#8645;</span></th>
        <th style="width:120px" onclick="sortBy('PASSIVO')">Passivo <span id="s_PASSIVO" class="si">&#8645;</span></th>
        <th style="width:160px" onclick="sortBy('DIV')">Diverg&#234;ncia <span id="s_DIV" class="si">&#8645;</span></th>
        <th style="width:88px" onclick="sortBy('DATA')">Data Lan&#231;. <span id="s_DATA" class="si">&#8645;</span></th>
        <th class="left nosort" style="width:120px">Gest&#227;o-UG Emitente</th>
        <th class="left" style="width:130px" onclick="sortBy('NUDOC')">N&#186; Documento <span id="s_NUDOC" class="si">&#8645;</span></th>
        <th class="left nosort" style="width:70px">Evento</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
      <tfoot><tr id="tfoot-row"><td class="left" colspan="10">&#8212;</td></tr></tfoot>
    </table>
  </div>
  <div id="pgbar-bot" class="pgbar"></div>
</div>

<script>
(function(){
const NOMES_MES={1:'Janeiro',2:'Fevereiro',3:'Mar\u00e7o',4:'Abril',5:'Maio',6:'Junho',
                 7:'Julho',8:'Agosto',9:'Setembro',10:'Outubro',11:'Novembro',12:'Dezembro'};
const EQUACOES=__EQUACOES__;
const UG_NAMES=__UG_NAMES__;
const GESTAO_NAMES=__GESTAO_NAMES__;
const CONTA_NAMES=__CONTA_NAMES__;
const PG_SZ=50;
let fil=[], filDocs=[], pg=1, sortCol='DATA', sortDir=1;
let emitSel='', filterMode='all';

function decomp(b64){
  const bin=atob(b64),buf=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++)buf[i]=bin.charCodeAt(i);
  const ds=new DecompressionStream('gzip'),wr=ds.writable.getWriter(),rd=ds.readable.getReader();
  wr.write(buf);wr.close();
  return new Promise(res=>{const c=[];rd.read().then(function p({done,value}){
    if(done)return res(new TextDecoder().decode(new Uint8Array(c.reduce((a,v)=>[...a,...v],[]))));
    c.push(value);rd.read().then(p);
  });});
}
decomp('__DADOS__').then(txt=>{
  const p=JSON.parse(txt);
  const cols=p.cols;
  window.ALL=p.rows.map(r=>Object.fromEntries(cols.map((k,i)=>[k,r[i]])));
  init();
});

function n(v){return v==null?0:+v||0;}
function brl(v){
  if(v==null||isNaN(v))return'\u2014';
  v=Math.round(v*100)/100;if(v===0)return'R$ 0,00';
  const s=v<0?'-':'',a=Math.abs(v);
  return s+'R$ '+a.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
}
function vc(v){return Math.abs(n(v))<0.005?'vz':n(v)>0?'vp':'vn';}
const fmtG=v=>v!=null?String(v).padStart(6,'0'):'\u2014';
function emitKey(r){return fmtG(r.COGESTAO_EMIT)+'-'+String(r.COUG_EMIT);}
function ugLabel(r){const nm=UG_NAMES[String(r.COUG)];return fmtG(r.COGESTAO)+' \u00b7 '+String(r.COUG)+(nm?' \u2014 '+nm:'');}
function isAtivo(conta){return EQUACOES.some(eq=>eq.ativo.includes(+conta));}
function isPassivo(conta){
  const s=String(conta);
  return EQUACOES.some(eq=>
    eq.passivo_exact.includes(+conta)||
    (eq.passivo_prefix&&s.startsWith(eq.passivo_prefix))
  );
}
function passivoEq(eq,byAcct){
  return Object.entries(byAcct).reduce((s,[acct,vl])=>{
    const a=String(acct);
    if(eq.passivo_exact.includes(+acct))return s+vl;
    if(eq.passivo_prefix&&a.startsWith(eq.passivo_prefix))return s+vl;
    return s;
  },0);
}

/* Autocomplete Gestao / UG emitente */
const AC={g:{sel:''},u:{sel:''}};
function onAC(k){
  const v=document.getElementById('f'+k+'-input').value.trim().toLowerCase();
  const dd=document.getElementById('f'+k+'-dd');
  let items=[];
  if(k==='g'){
    const m=new Map();
    const gIds=[...new Set(window.ALL.map(r=>String(r.COGESTAO_EMIT)))].sort();
    items=gIds.filter(id=>{const nm=GESTAO_NAMES[id]||'';return !v||fmtG(id).includes(v)||nm.toLowerCase().includes(v);}).slice(0,60)
      .map(id=>{const nm=GESTAO_NAMES[id]||'';const lbl=(fmtG(id)+(nm?' - '+nm:'')).replace(/'/g,"\\'");
        return '<div class="ac-dd-item" onclick="selAC(\'g\',\''+id+'\',\''+lbl+'\')">'
          +'<strong>'+fmtG(id)+'</strong>'+(nm?' - '+nm:'')+'</div>';});
  }else{
    const uIds=[...new Set(window.ALL.map(r=>String(r.COUG_EMIT)))].sort();
    items=uIds.filter(id=>{const nm=UG_NAMES[id]||'';return !v||id.includes(v)||nm.toLowerCase().includes(v);}).slice(0,60)
      .map(id=>{const nm=UG_NAMES[id]||'';const lbl=(id+(nm?' \u2014 '+nm:'')).replace(/'/g,"\\'");
        return '<div class="ac-dd-item" onclick="selAC(\'u\',\''+id+'\',\''+lbl+'\')">'
          +'<strong>'+id+'</strong>'+(nm?' \u2014 '+nm:'')+'</div>';});
  }
  dd.innerHTML=items.length?items.join(''):'<div class="ac-dd-empty">Nenhum resultado</div>';
  dd.style.display='block';
}
function selAC(k,val,label){
  AC[k].sel=String(val);
  document.getElementById('f'+k+'-input').value=label;
  document.getElementById('f'+k+'-clear').style.display='block';
  document.getElementById('f'+k+'-dd').style.display='none';
  aplicar();
}
function limparAC(k){
  AC[k].sel='';
  document.getElementById('f'+k+'-input').value='';
  document.getElementById('f'+k+'-clear').style.display='none';
  document.getElementById('f'+k+'-dd').style.display='none';
  aplicar();
}
function blurAC(k){setTimeout(()=>document.getElementById('f'+k+'-dd').style.display='none',200);}

/* Autocomplete Emitente inline */
let emitList=[];
function buildEmitList(){emitList=[...new Set(window.ALL.map(r=>emitKey(r)))].sort();}
function onEmit(){
  const v=document.getElementById('emit-in').value.trim().toLowerCase();
  const m=v?emitList.filter(e=>e.toLowerCase().includes(v)):emitList;
  const dd=document.getElementById('emit-dd');
  dd.innerHTML=m.length?m.slice(0,60).map(e=>'<div class="ac-dd-item" onmousedown="selEmit(\''+e+'\')">'+e+'</div>').join(''):'<div class="ac-dd-empty">Nenhum resultado</div>';
  dd.style.display='block';
}
function selEmit(v){emitSel=v;document.getElementById('emit-in').value=v;document.getElementById('emit-clr').style.display='block';document.getElementById('emit-dd').style.display='none';aplicar();}
function limparEmit(){emitSel='';document.getElementById('emit-in').value='';document.getElementById('emit-clr').style.display='none';aplicar();}
function blurEmit(){setTimeout(()=>document.getElementById('emit-dd').style.display='none',150);}

function filtrarEq(mode){
  filterMode=(filterMode===mode)?'all':mode;
  document.getElementById('btn-eq1').classList.toggle('active',filterMode==='eq1');
  document.getElementById('btn-eq2').classList.toggle('active',filterMode==='eq2');
  document.getElementById('btn-eq3').classList.toggle('active',filterMode==='eq3');
  document.getElementById('btn-eq4').classList.toggle('active',filterMode==='eq4');
  document.getElementById('btn-eq5').classList.toggle('active',filterMode==='eq5');
  document.getElementById('btn-eq6').classList.toggle('active',filterMode==='eq6');
  document.getElementById('btn-eq7').classList.toggle('active',filterMode==='eq7');
  aplicar();
}

/* Ordenacao */
const SORT_COLS=['UG','ATIVO','PASSIVO','DIV','DATA','NUDOC'];
function sortBy(col){
  if(sortCol===col){sortDir*=-1;}else{sortCol=col;sortDir=1;}
  SORT_COLS.forEach(c=>{const el=document.getElementById('s_'+c);if(el)el.textContent=c===sortCol?(sortDir>0?'\u2191':'\u2193'):'\u21c5';});
  filDocs.sort(cmpDocs);pg=1;render();
}
function cmpDocs(a,b){
  if(sortCol==='DATA')    return sortDir*(a.minDataISO||'').localeCompare(b.minDataISO||'');
  if(sortCol==='NUDOC')   return sortDir*String(a.NUDOCUMENTO).localeCompare(String(b.NUDOCUMENTO),'pt-BR');
  if(sortCol==='ATIVO')   return sortDir*(n(a.totAtivo)-n(b.totAtivo));
  if(sortCol==='PASSIVO') return sortDir*(n(a.totPassivo)-n(b.totPassivo));
  if(sortCol==='DIV')     return sortDir*(Math.abs(n(a.maxDiv))-Math.abs(n(b.maxDiv)));
  if(sortCol==='UG')      return sortDir*(a.emitKey||'').localeCompare(b.emitKey||'','pt-BR');
  return 0;
}

function init(){
  buildEmitList();
  const meses=[...new Set(window.ALL.map(r=>r.INMES))].sort((a,b)=>a-b);
  const fm=document.getElementById('fm');
  meses.forEach(m=>{const o=document.createElement('option');o.value=m;o.textContent=NOMES_MES[m]||m;fm.appendChild(o);});
  fm.addEventListener('change',aplicar);
  document.getElementById('busca').addEventListener('input',aplicar);
  const feq=document.getElementById('feq');
  EQUACOES.forEach(eq=>{
    const o=document.createElement('option');o.value=eq.id;o.textContent=eq.id+' \u2014 '+eq.desc;feq.appendChild(o);
    const ks=document.getElementById('ks-'+eq.id.toLowerCase());if(ks)ks.textContent=eq.desc;
  });
  feq.addEventListener('change',aplicar);
  const el=document.getElementById('s_DATA');if(el)el.textContent='\u2191';
  aplicar();
}

function aplicar(){
  const fmes=document.getElementById('fm').value;
  const feq=document.getElementById('feq').value;
  const busca=(document.getElementById('busca').value||'').trim().toLowerCase();
  const eqFil=feq?EQUACOES.find(e=>e.id===feq):null;
  fil=window.ALL.filter(r=>{
    if(AC.g.sel&&String(r.COGESTAO_EMIT)!==AC.g.sel)return false;
    if(AC.u.sel&&String(r.COUG_EMIT)!==AC.u.sel)return false;
    if(fmes&&String(r.INMES)!==fmes)return false;
    if(eqFil){
      const c=+r.COCONTACONTABIL,s=String(r.COCONTACONTABIL);
      const inEq=eqFil.ativo.includes(c)||eqFil.passivo_exact.includes(c)||(eqFil.passivo_prefix&&s.startsWith(eqFil.passivo_prefix));
      if(!inEq)return false;
    }
    return true;
  });
  const map={};
  fil.forEach(r=>{
    const ek=emitKey(r);
    const k=ek+'|'+(r.NUDOCUMENTO||'');
    if(!map[k])map[k]={key:k,emitKey:ek,NUDOCUMENTO:r.NUDOCUMENTO,rows:[],
                       minData:null,minDataISO:null,
                       totAtivo:0,totPassivo:0,divEQ1:0,divEQ2:0,divEQ3:0,divEQ4:0,divEQ5:0,divEQ6:0,divEQ7:0,byAcct:{}};
    const d=map[k];
    d.rows.push(r);
    const vl=n(r.VLNET),acct=+r.COCONTACONTABIL;
    d.byAcct[acct]=(d.byAcct[acct]||0)+vl;
    if(isAtivo(acct))  d.totAtivo  =Math.round((d.totAtivo  +vl)*100)/100;
    if(isPassivo(acct))d.totPassivo=Math.round((d.totPassivo+vl)*100)/100;
    if(!d.minDataISO||r.DATA_ISO<d.minDataISO){d.minDataISO=r.DATA_ISO;d.minData=r.DATA;}
  });
  Object.values(map).forEach(d=>{
    EQUACOES.forEach(eq=>{
      const pas=Math.round(passivoEq(eq,d.byAcct)*100)/100;
      const ati=Math.round(eq.ativo.reduce((s,c)=>s+(d.byAcct[c]||0),0)*100)/100;
      const div=Math.round((pas-ati)*100)/100;
      if(eq.id==='EQ1')d.divEQ1=div;
      if(eq.id==='EQ2')d.divEQ2=div;
      if(eq.id==='EQ3')d.divEQ3=div;
      if(eq.id==='EQ4')d.divEQ4=div;
      if(eq.id==='EQ5')d.divEQ5=div;
      if(eq.id==='EQ6')d.divEQ6=div;
      if(eq.id==='EQ7')d.divEQ7=div;
    });
    d.maxDiv=[d.divEQ1,d.divEQ2,d.divEQ3,d.divEQ4,d.divEQ5,d.divEQ6,d.divEQ7].reduce((m,v)=>Math.abs(v)>Math.abs(m)?v:m,0);
  });
  filDocs=Object.values(map).filter(d=>{
    if(emitSel&&d.emitKey!==emitSel)return false;
    if(filterMode==='eq1'&&Math.abs(d.divEQ1)<0.005)return false;
    if(filterMode==='eq2'&&Math.abs(d.divEQ2)<0.005)return false;
    if(filterMode==='eq3'&&Math.abs(d.divEQ3)<0.005)return false;
    if(filterMode==='eq4'&&Math.abs(d.divEQ4)<0.005)return false;
    if(filterMode==='eq5'&&Math.abs(d.divEQ5)<0.005)return false;
    if(filterMode==='eq6'&&Math.abs(d.divEQ6)<0.005)return false;
    if(filterMode==='eq7'&&Math.abs(d.divEQ7)<0.005)return false;
    if(busca){
      const docOk=String(d.NUDOCUMENTO||'').toLowerCase().includes(busca);
      const evOk=d.rows.some(r=>r.COEVENTO!=null&&String(r.COEVENTO).toLowerCase().includes(busca));
      if(!docOk&&!evOk)return false;
    }
    return true;
  });
  filDocs.sort(cmpDocs);pg=1;render();kpis();
}

function render(){
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot-row');
  const start=(pg-1)*PG_SZ,end=Math.min(start+PG_SZ,filDocs.length);
  let html='';
  filDocs.slice(start,end).forEach(d=>{
    const did='d'+d.key.replace(/[^a-z0-9]/gi,'_');
    const totA=n(d.totAtivo),totP=n(d.totPassivo);
    const _dp=[];
    if(Math.abs(n(d.divEQ1))>=0.005)_dp.push('EQ1\u00a0'+brl(n(d.divEQ1)));
    if(Math.abs(n(d.divEQ2))>=0.005)_dp.push('EQ2\u00a0'+brl(n(d.divEQ2)));
    if(Math.abs(n(d.divEQ3))>=0.005)_dp.push('EQ3\u00a0'+brl(n(d.divEQ3)));
    if(Math.abs(n(d.divEQ4))>=0.005)_dp.push('EQ4\u00a0'+brl(n(d.divEQ4)));
    if(Math.abs(n(d.divEQ5))>=0.005)_dp.push('EQ5\u00a0'+brl(n(d.divEQ5)));
    if(Math.abs(n(d.divEQ6))>=0.005)_dp.push('EQ6\u00a0'+brl(n(d.divEQ6)));
    if(Math.abs(n(d.divEQ7))>=0.005)_dp.push('EQ7\u00a0'+brl(n(d.divEQ7)));
    const divStr=_dp.length?_dp.join(' / '):'\u2014';
    const hasdiv=_dp.length>0;
    html+='<tr class="row-doc" onclick="toggleDoc(\''+did+'\')">'
      +'<td class="left" colspan="3"><span class="tog" id="tog_'+did+'">&#9654;</span>'
      +' <code style="color:#9ab0cc;font-size:12px">'+String(d.NUDOCUMENTO||'')+'</code>'
      +' <span style="opacity:.6">&middot;</span>'
      +' <code style="color:#c8d8ec;font-size:12px">'+d.emitKey+'</code>'
      +' <span style="font-size:10px;font-weight:400;opacity:.45">('+d.rows.length+' lan\u00e7.)</span></td>'
      +'<td class="'+vc(totA)+'">'+brl(totA)+'</td>'
      +'<td class="'+vc(totP)+'">'+brl(totP)+'</td>'
      +'<td class="'+(hasdiv?'vp':'vz')+'" style="font-size:11px;white-space:nowrap">'+divStr+'</td>'
      +'<td style="font-size:11px">'+(d.minData||'\u2014')+'</td>'
      +'<td class="left"></td><td class="left"></td><td class="left"></td>'
      +'</tr>';
    const rows=[...d.rows].sort((a,b)=>(a.DATA_ISO||'').localeCompare(b.DATA_ISO||'')||String(a.COCONTACONTABIL).localeCompare(String(b.COCONTACONTABIL)));
    rows.forEach((r,j)=>{
      const vl=n(r.VLNET),acct=+r.COCONTACONTABIL;
      const vlA=isAtivo(acct)?vl:0,vlP=isPassivo(acct)?vl:0;
      const evStr=r.COEVENTO!=null?String(r.COEVENTO):'\u2014';
      const contaNome=CONTA_NAMES[r.COCONTACONTABIL]?' <span style="font-size:10px;opacity:.65">'+CONTA_NAMES[r.COCONTACONTABIL]+'</span>':'';
      html+='<tr class="dr'+(j%2?' alt':'')+'" data-doc="'+did+'">'
        +'<td></td>'
        +'<td class="left" style="font-size:11px;padding-left:24px">'+ugLabel(r)+'</td>'
        +'<td class="left"><code style="font-size:11px;color:var(--navy)">'+r.COCONTACONTABIL+'</code>'+contaNome+'</td>'
        +'<td class="'+(Math.abs(vlA)<0.005?'vz':vc(vlA))+'">'+(Math.abs(vlA)<0.005?'\u2014':brl(vlA))+'</td>'
        +'<td class="'+(Math.abs(vlP)<0.005?'vz':vc(vlP))+'">'+(Math.abs(vlP)<0.005?'\u2014':brl(vlP))+'</td>'
        +'<td class="vz">\u2014</td>'
        +'<td style="font-size:11px">'+(r.DATA||'\u2014')+'</td>'
        +'<td class="left"><code style="font-size:11px">'+emitKey(r)+'</code></td>'
        +'<td class="left"><code style="font-size:11px">'+String(r.NUDOCUMENTO||'')+'</code></td>'
        +'<td class="left" style="font-size:11px">'+evStr+'</td>'
        +'</tr>';
    });
  });
  tb.innerHTML=html||'<tr><td colspan="10" style="text-align:center;padding:24px;color:var(--muted)">Nenhum documento encontrado.</td></tr>';
  const totA=filDocs.reduce((s,d)=>s+n(d.totAtivo),0);
  const totP=filDocs.reduce((s,d)=>s+n(d.totPassivo),0);
  const totDiv=filDocs.reduce((s,d)=>s+n(d.maxDiv),0);
  tf.innerHTML='<td class="left" colspan="3">Total &middot; '+filDocs.length.toLocaleString('pt-BR')+' documento(s)</td>'
    +'<td class="'+vc(totA)+'">'+brl(totA)+'</td>'
    +'<td class="'+vc(totP)+'">'+brl(totP)+'</td>'
    +'<td class="'+vc(totDiv)+'">'+brl(totDiv)+'</td>'
    +'<td colspan="4"></td>';
  document.getElementById('ttitle').textContent=filDocs.length.toLocaleString('pt-BR')+' documento(s)';
  paginar();
}

function toggleDoc(id){
  const tog=document.getElementById('tog_'+id);
  const open=tog&&tog.textContent.includes('\u25bc');
  document.querySelectorAll('[data-doc="'+id+'"]').forEach(tr=>tr.style.display=open?'none':'table-row');
  if(tog)tog.innerHTML=open?'&#9654;':'&#9660;';
}

function kpis(){
  const totA=filDocs.reduce((s,d)=>s+n(d.totAtivo),0);
  const totP=filDocs.reduce((s,d)=>s+n(d.totPassivo),0);
  const totD1=filDocs.reduce((s,d)=>s+n(d.divEQ1),0);
  const totD2=filDocs.reduce((s,d)=>s+n(d.divEQ2),0);
  const totD3=filDocs.reduce((s,d)=>s+n(d.divEQ3),0);
  const totD4=filDocs.reduce((s,d)=>s+n(d.divEQ4),0);
  const totD5=filDocs.reduce((s,d)=>s+n(d.divEQ5),0);
  const totD6=filDocs.reduce((s,d)=>s+n(d.divEQ6),0);
  const totD7=filDocs.reduce((s,d)=>s+n(d.divEQ7),0);
  document.getElementById('kv-ativo').innerHTML='<span class="'+vc(totA)+'">'+brl(totA)+'</span>';
  document.getElementById('kv-passivo').innerHTML='<span class="'+vc(totP)+'">'+brl(totP)+'</span>';
  document.getElementById('kv-eq1').innerHTML='<span class="'+vc(totD1)+'">'+brl(totD1)+'</span>';
  document.getElementById('kv-eq2').innerHTML='<span class="'+vc(totD2)+'">'+brl(totD2)+'</span>';
  document.getElementById('kv-eq3').innerHTML='<span class="'+vc(totD3)+'">'+brl(totD3)+'</span>';
  document.getElementById('kv-eq4').innerHTML='<span class="'+vc(totD4)+'">'+brl(totD4)+'</span>';
  document.getElementById('kv-eq5').innerHTML='<span class="'+vc(totD5)+'">'+brl(totD5)+'</span>';
  document.getElementById('kv-eq6').innerHTML='<span class="'+vc(totD6)+'">'+brl(totD6)+'</span>';
  document.getElementById('kv-eq7').innerHTML='<span class="'+vc(totD7)+'">'+brl(totD7)+'</span>';
  document.getElementById('ks-ativo').textContent=filDocs.length.toLocaleString('pt-BR')+' documento(s) no filtro';
  document.getElementById('ks-passivo').textContent=filDocs.length.toLocaleString('pt-BR')+' documento(s) no filtro';
  document.getElementById('kpi-eq1').className='kpi'+(Math.abs(totD1)>0.005?' ka':'');
  document.getElementById('kpi-eq2').className='kpi'+(Math.abs(totD2)>0.005?' km':'');
  document.getElementById('kpi-eq3').className='kpi'+(Math.abs(totD3)>0.005?' kg':'');
  document.getElementById('kpi-eq4').className='kpi'+(Math.abs(totD4)>0.005?' kv4':'');
  document.getElementById('kpi-eq5').className='kpi'+(Math.abs(totD5)>0.005?' kv5':'');
  document.getElementById('kpi-eq6').className='kpi'+(Math.abs(totD6)>0.005?' kv6':'');
  document.getElementById('kpi-eq7').className='kpi'+(Math.abs(totD7)>0.005?' kv7':'');
}

function paginar(){
  const pages=Math.ceil(filDocs.length/PG_SZ)||1;
  const h=_pgHtml(pages);
  document.getElementById('pgbar-top').innerHTML=h;
  document.getElementById('pgbar-bot').innerHTML=h;
}
function _pgHtml(pages){
  if(pages<=1)return'';
  const s=(pg-1)*PG_SZ+1,e=Math.min(pg*PG_SZ,filDocs.length);
  let b='<button onclick="ir(pg-1)" '+(pg===1?'disabled':'')+'>&#8249;</button>';
  for(let i=1;i<=pages;i++){
    if(i===1||i===pages||Math.abs(i-pg)<=1)b+='<button '+(i===pg?'class="pgcur"':'')+' onclick="ir('+i+')">'+i+'</button>';
    else if(Math.abs(i-pg)===2)b+='<button disabled style="border:none;background:none;padding:4px">&#8230;</button>';
  }
  b+='<button onclick="ir(pg+1)" '+(pg===pages?'disabled':'')+'>&#8250;</button>';
  return '<span style="font-size:11px;color:var(--muted)">'+s+'\u2013'+e+' de '+filDocs.length.toLocaleString('pt-BR')+'</span><div style="display:flex;gap:4px;flex-wrap:wrap">'+b+'</div>';
}
function ir(p){const pages=Math.ceil(filDocs.length/PG_SZ);if(p<1||p>pages)return;pg=p;render();window.scrollTo({top:0,behavior:'smooth'});}

function exportarCSV(){
  const hdrs=['Mes','Gestao Emit','UG Emit','Nome UG Emit','Gestao Contab','UG Contab','Nome UG Contab',
              'Documento','Conta','Nome Conta','Ativo','Passivo','Data','Evento'];
  const cel=v=>{if(typeof v==='number')return String(v).replace('.',',');const s=(v||'').toString().trim();return /[;"\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
  const linhas=[hdrs.join(';')];
  fil.forEach(r=>{
    const vl=n(r.VLNET),acct=+r.COCONTACONTABIL;
    linhas.push([r.INMES,fmtG(r.COGESTAO_EMIT),r.COUG_EMIT,(UG_NAMES[String(r.COUG_EMIT)]||''),
                 fmtG(r.COGESTAO),r.COUG,(UG_NAMES[String(r.COUG)]||''),
                 r.NUDOCUMENTO||'',r.COCONTACONTABIL,(CONTA_NAMES[r.COCONTACONTABIL]||''),
                 cel(isAtivo(acct)?vl:0),cel(isPassivo(acct)?vl:0),
                 r.DATA||'',r.COEVENTO!=null?r.COEVENTO:''].join(';'));
  });
  const a=Object.assign(document.createElement('a'),{
    href:URL.createObjectURL(new Blob(['\ufeff'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),
    download:'correspondencia_ativo_passivo.csv'
  });
  document.body.appendChild(a);a.click();a.remove();
}

function limpar(){
  ['g','u'].forEach(k=>limparAC(k));limparEmit();
  document.getElementById('fm').value='';
  document.getElementById('busca').value='';
  filterMode='all';
  document.getElementById('btn-eq1').classList.remove('active');
  document.getElementById('btn-eq2').classList.remove('active');
  document.getElementById('btn-eq3').classList.remove('active');
  document.getElementById('btn-eq4').classList.remove('active');
  document.getElementById('btn-eq5').classList.remove('active');
  document.getElementById('btn-eq6').classList.remove('active');
  document.getElementById('btn-eq7').classList.remove('active');
  document.getElementById('feq').value='';
  aplicar();
}

window.onAC=onAC;window.selAC=selAC;window.limparAC=limparAC;window.blurAC=blurAC;
window.onEmit=onEmit;window.selEmit=selEmit;window.limparEmit=limparEmit;window.blurEmit=blurEmit;
window.toggleDoc=toggleDoc;window.sortBy=sortBy;window.filtrarEq=filtrarEq;
window.aplicar=aplicar;window.limpar=limpar;window.exportarCSV=exportarCSV;window.ir=ir;
})();
</script>
</body>
</html>"""


# ── Extração ──────────────────────────────────────────────────────────────────
def extrair():
    oracledb.init_oracle_client(lib_dir=INSTANT_CLIENT_DIR)
    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle…")
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        print(f"[{datetime.now():%H:%M:%S}] Executando consulta (todos os meses)…")
        df       = pd.read_sql(SQL,        conn)
        df_ug    = pd.read_sql(SQL_UG,     conn)
        df_gest  = pd.read_sql(SQL_GESTAO, conn)
        df_conta = pd.read_sql(SQL_CONTA,  conn)
    print(f"  {len(df):,} linhas ({df['COUG_EMIT'].nunique()} UGs emitentes, "
          f"{df['INMES'].nunique()} meses).")
    ug_names    = dict(zip(df_ug["COUG"].astype(str),    df_ug["NOUG"]))
    gestao_names= dict(zip(df_gest["COGESTAO"].astype(str), df_gest["NOGESTAO"]))
    conta_names = dict(zip(df_conta["COCONTACONTABIL"].astype(str), df_conta["NOCONTACONTABIL"]))
    return df, ug_names, gestao_names, conta_names


# ── Geração do HTML ───────────────────────────────────────────────────────────
def gerar_html(df: pd.DataFrame, ug_names: dict, gestao_names: dict, conta_names: dict) -> str:
    cols = list(df.columns)
    rows = []
    for r in df.itertuples(index=False):
        row = []
        for v in r:
            if isinstance(v, float) and pd.isna(v):
                row.append(None)
            elif hasattr(v, "item"):
                row.append(v.item())
            else:
                row.append(v)
        rows.append(row)

    payload   = {"cols": cols, "rows": rows}
    json_str  = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    dados_b64 = base64.b64encode(
        gzip.compress(json_str.encode("utf-8"), compresslevel=9)
    ).decode()
    print(f"  JSON: {len(json_str)//1024} KB → comprimido: {len(dados_b64)//1024} KB")

    eq_js      = json.dumps(EQUACOES,     ensure_ascii=False)
    ug_js      = json.dumps(ug_names,     ensure_ascii=False)
    gestao_js  = json.dumps(gestao_names, ensure_ascii=False)
    conta_js   = json.dumps(conta_names,  ensure_ascii=False)
    ts         = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    html = HTML_TEMPLATE
    html = html.replace("__EQUACOES__",    eq_js)
    html = html.replace("__UG_NAMES__",    ug_js)
    html = html.replace("__GESTAO_NAMES__",gestao_js)
    html = html.replace("__CONTA_NAMES__", conta_js)
    html = html.replace("'__DADOS__'",     "'" + dados_b64 + "'")
    html = html.replace("__TIMESTAMP__",   ts)
    return html


# ── Publicação ────────────────────────────────────────────────────────────────
def publicar(html_path: Path, no_push: bool) -> None:
    print(f"  HTML salvo: {html_path}")
    if no_push or os.environ.get("NO_GIT_PUSH") == "1":
        print("  Push ignorado (--no-push ou NO_GIT_PUSH=1).")
        return
    pasta = str(html_path.parent)
    subprocess.run(["git", "-C", pasta, "add", html_path.name], check=True)
    subprocess.run(["git", "-C", pasta, "commit", "-m",
                    f"auto: atualiza {ARQUIVO_HTML}"], check=True)
    subprocess.run(["git", "-C", pasta, "push", "origin",
                    os.environ.get("GITHUB_BRANCH", "main")], check=True)


# ── Principal ─────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()

    print(f"[{datetime.now():%H:%M:%S}] Correspondência Ativo x Passivo — por lançamento…")
    df, ug_names, gestao_names, conta_names = extrair()
    print(f"[{datetime.now():%H:%M:%S}] Gerando HTML…")
    html = gerar_html(df, ug_names, gestao_names, conta_names)

    html_path = PASTA / ARQUIVO_HTML
    html_path.write_text(html, encoding="utf-8")
    publicar(html_path, a.no_push)
    print(f"[{datetime.now():%H:%M:%S}] Concluído.")


if __name__ == "__main__":
    main()
