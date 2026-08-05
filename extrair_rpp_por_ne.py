"""
Controle de NE de Anos Anteriores — SIGGO/Oracle
Verifica equivalência entre contas patrimoniais (2XXXXXXXX),
de controle (827XXXXXX) e orçamentárias (632XXXXXX) por UG e NE,
para Notas de Empenho de exercícios anteriores a 2026.

Regra de saldo aplicada em todo o sistema:
    INSALDOCONTABIL = 'C' (credora) → VACREDITO - VADEBITO
    INSALDOCONTABIL = 'D' (devedora) → VADEBITO - VACREDITO

NE extraída via SUBSTR(COCONTACORRENTE, 1, 11) para todos os tipos:
    Tipo '63'/'76' (2XXXXXXXX): ex. "2024NE00459100000000FP..." → "2024NE00459"
    Tipo '16'     (632/827)  : ex. "2024NE00577            " → "2024NE00577"

Uso:
    python extrair_controle_ne_anteriores.py
    python extrair_controle_ne_anteriores.py --ug 10101
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import oracledb
import pandas as pd

import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ORACLE_USER = os.environ["ORACLE_USER"]
ORACLE_PASS = os.environ["ORACLE_PASS"]
ORACLE_DSN  = os.environ["ORACLE_DSN"]
SCHEMA      = "MIL2026."

ARQUIVO_HTML = "rpp_por_ne.html"

oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")

SQL = """
WITH base AS (
    SELECT
        s.COUG,
        s.COGESTAO,
        s.COCONTACONTABIL,
        TRIM(SUBSTR(s.COCONTACORRENTE, 1, 11)) AS NE,
        CASE
            WHEN cc.INSALDOCONTABIL = 'C' THEN s.VACREDITO - s.VADEBITO
            WHEN cc.INSALDOCONTABIL = 'D' THEN s.VADEBITO - s.VACREDITO
            ELSE 0
        END AS SALDO
    FROM {schema}VSALDOCONTABIL s
    JOIN {schema}VCONTACONTABIL cc
        ON cc.COCONTACONTABIL = s.COCONTACONTABIL
    WHERE
        REGEXP_LIKE(s.COCONTACORRENTE, '^[0-9]{4}')
        AND TO_NUMBER(SUBSTR(s.COCONTACORRENTE, 1, 4)) < 2026
        AND (
            (    s.COCONTACONTABIL BETWEEN 200000000 AND 299999999
             AND cc.INSISCONTABIL = 'F'
             AND cc.INCONTACORRENTE IN ('63', '76')
            )
            OR
            (    s.COCONTACONTABIL IN (632110100, 632110300, 632110400)
             AND cc.INCONTACORRENTE = '16'
            )
            OR
            (    s.COCONTACONTABIL IN (827110301, 827110303)
             AND cc.INCONTACORRENTE = '16'
            )
            OR
            (    s.COCONTACONTABIL IN (631200000, 631300000, 631810000)
             AND cc.INCONTACORRENTE = '16'
            )
        )
        {filtro_ug}
),
saldos AS (
    SELECT
        COUG,
        COGESTAO,
        NE,
        ROUND(SUM(CASE WHEN COCONTACONTABIL BETWEEN 200000000 AND 299999999
                            AND COCONTACONTABIL NOT BETWEEN 218810000 AND 218819999
                            AND COCONTACONTABIL NOT BETWEEN 218820000 AND 218829999
                            AND COCONTACONTABIL NOT BETWEEN 218830000 AND 218839999
                       THEN SALDO ELSE 0 END), 2) AS S_2,
        ROUND(SUM(CASE WHEN COCONTACONTABIL BETWEEN 218810000 AND 218819999
                       THEN SALDO ELSE 0 END), 2) AS S_21881,
        ROUND(SUM(CASE WHEN COCONTACONTABIL BETWEEN 218830000 AND 218839999
                       THEN SALDO ELSE 0 END), 2) AS S_21883,
        ROUND(SUM(CASE WHEN COCONTACONTABIL BETWEEN 218820000 AND 218829999
                       THEN SALDO ELSE 0 END), 2) AS S_21882,
        ROUND(SUM(CASE WHEN COCONTACONTABIL = 827110301 THEN SALDO ELSE 0 END), 2) AS S_827110301,
        ROUND(SUM(CASE WHEN COCONTACONTABIL = 827110303 THEN SALDO ELSE 0 END), 2) AS S_827110303,
        ROUND(SUM(CASE WHEN COCONTACONTABIL = 632110100 THEN SALDO ELSE 0 END), 2) AS S_632110100,
        ROUND(SUM(CASE WHEN COCONTACONTABIL = 632110300 THEN SALDO ELSE 0 END), 2) AS S_632110300,
        ROUND(SUM(CASE WHEN COCONTACONTABIL = 632110400 THEN SALDO ELSE 0 END), 2) AS S_632110400,
        ROUND(SUM(CASE WHEN COCONTACONTABIL = 631810000 THEN SALDO ELSE 0 END), 2) AS S_631810000,
        ROUND(SUM(CASE WHEN COCONTACONTABIL = 631200000 THEN SALDO ELSE 0 END), 2) AS S_631200000,
        ROUND(SUM(CASE WHEN COCONTACONTABIL = 631300000 THEN SALDO ELSE 0 END), 2) AS S_631300000
    FROM base
    GROUP BY COUG, COGESTAO, NE
)
SELECT
    s.COUG,
    NVL(ug.NOUG, 'Sem nome')    AS NOUG,
    s.COGESTAO,
    NVL(g.NOGESTAO, 'Sem nome') AS NOGESTAO,
    s.NE,
    s.S_2,
    s.S_21881,
    s.S_21883,
    s.S_21882,
    s.S_827110301,
    s.S_827110303,
    s.S_632110100,
    s.S_632110300,
    s.S_632110400,
    s.S_631810000,
    s.S_631200000,
    s.S_631300000,
    ROUND(s.S_21881 + s.S_21883, 2)                                                                   AS RETEM_A,
    ROUND((s.S_21881 + s.S_21883) - s.S_827110301, 2)                                                AS DIF_A,
    ROUND(s.S_21882 - s.S_827110303, 2)                                                               AS DIF_B,
    ROUND((s.S_827110301 + s.S_827110303) - (s.S_632110300 + s.S_632110400), 2)                      AS DIF_C,
    ROUND((s.S_21881 + s.S_21883 + s.S_21882) - (s.S_632110300 + s.S_632110400 + s.S_631810000), 2) AS DIF_D,
    ROUND(s.S_2 - (s.S_632110100 + s.S_631200000 + s.S_631300000), 2)                               AS DIF_E
FROM saldos s
LEFT JOIN {schema}UNIDADEGESTORA ug ON ug.COUG  = s.COUG
LEFT JOIN {schema}GESTAO          g  ON g.COGESTAO = s.COGESTAO
WHERE
       ABS(s.S_2)         > 0.005
    OR ABS(s.S_21881)     > 0.005
    OR ABS(s.S_21882)     > 0.005
    OR ABS(s.S_21883)     > 0.005
    OR ABS(s.S_827110301) > 0.005
    OR ABS(s.S_827110303) > 0.005
    OR ABS(s.S_632110100) > 0.005
    OR ABS(s.S_632110300) > 0.005
    OR ABS(s.S_632110400) > 0.005
    OR ABS(s.S_631810000) > 0.005
    OR ABS(s.S_631200000) > 0.005
    OR ABS(s.S_631300000) > 0.005
ORDER BY s.COUG, s.COGESTAO, s.NE
"""

SQL_DETALHE = """
SELECT
    s.COUG,
    TRIM(SUBSTR(s.COCONTACORRENTE, 1, 11)) AS NE,
    s.COCONTACONTABIL,
    NVL(cc.NOCONTACONTABIL, 'Sem nome') AS NOCONTACONTABIL,
    ROUND(
        CASE
            WHEN cc.INSALDOCONTABIL = 'C' THEN SUM(s.VACREDITO - s.VADEBITO)
            WHEN cc.INSALDOCONTABIL = 'D' THEN SUM(s.VADEBITO - s.VACREDITO)
            ELSE 0
        END, 2) AS SALDO
FROM {schema}VSALDOCONTABIL s
JOIN {schema}VCONTACONTABIL cc ON cc.COCONTACONTABIL = s.COCONTACONTABIL
WHERE
    REGEXP_LIKE(s.COCONTACORRENTE, '^[0-9]{4}')
    AND TO_NUMBER(SUBSTR(s.COCONTACORRENTE, 1, 4)) < 2026
    AND (
        (s.COCONTACONTABIL BETWEEN 200000000 AND 299999999
         AND cc.INSISCONTABIL = 'F'
         AND cc.INCONTACORRENTE IN ('63', '76'))
        OR (s.COCONTACONTABIL IN (632110100, 632110300, 632110400) AND cc.INCONTACORRENTE = '16')
        OR (s.COCONTACONTABIL IN (827110301, 827110303) AND cc.INCONTACORRENTE = '16')
        OR (s.COCONTACONTABIL IN (631200000, 631300000, 631810000) AND cc.INCONTACORRENTE = '16')
    )
    {filtro_ug}
GROUP BY s.COUG, TRIM(SUBSTR(s.COCONTACORRENTE, 1, 11)),
         s.COCONTACONTABIL, cc.NOCONTACONTABIL, cc.INSALDOCONTABIL
HAVING ABS(CASE
    WHEN cc.INSALDOCONTABIL = 'C' THEN SUM(s.VACREDITO - s.VADEBITO)
    WHEN cc.INSALDOCONTABIL = 'D' THEN SUM(s.VADEBITO - s.VACREDITO)
    ELSE 0 END) > 0.005
ORDER BY s.COUG, TRIM(SUBSTR(s.COCONTACORRENTE, 1, 11)), s.COCONTACONTABIL
"""

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RPNP e RPP por Nota de Empenho</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --navy:#0d1b3e;--navy-mid:#162550;--navy-light:#1e3267;
  --teal:#0090a8;--teal-light:#00b8d4;
  --surface:#fff;--bg:#f2f5f9;--border:#dce3ed;
  --row-alt:#f7f9fc;--hover:#eaf4f7;
  --text:#1a2033;--muted:#6b7a99;
  --red:#c0392b;--green:#1a7a44;--radius:10px;
  --shadow:0 2px 12px rgba(13,27,62,.10);
  --eq-a:#5b9bd5;--eq-b:#70ad47;--eq-c:#ed7d31;
}
body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}
header{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}
.hlogo{width:32px;height:32px;background:var(--teal);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}
header h1{font-size:14px;font-weight:700;letter-spacing:.6px;text-transform:uppercase}
header h1 span{font-weight:400;color:#9ab0cc;font-size:12px;display:block;text-transform:none;letter-spacing:0;margin-top:1px}
#ts{font-size:11px;color:#7a99bb;white-space:nowrap}
.aviso{background:#fff8e6;border-bottom:1px solid #f0c060;padding:8px 28px;font-size:11.5px;color:#7a5c00;display:flex;align-items:center;gap:8px}
/* Filtros */
.fbar{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
.fg{display:flex;flex-direction:column;gap:4px}
.fg label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.fg select{border:1.5px solid var(--border);border-radius:6px;padding:7px 28px 7px 10px;font-size:12.5px;min-width:140px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E") no-repeat right 9px center;color:var(--text);cursor:pointer;appearance:none}
.fg select:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-wrap{position:relative;min-width:220px}
.ac-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text);transition:border-color .15s}
.ac-input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}
.ac-dd{position:absolute;top:calc(100% + 4px);left:0;right:0;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:240px;overflow-y:auto;display:none}
.ac-dd-item{padding:8px 12px;cursor:pointer;font-size:12.5px;border-bottom:1px solid var(--border);transition:background .1s}
.ac-dd-item:last-child{border-bottom:none}
.ac-dd-item:hover{background:var(--hover)}
.ac-dd-item strong{color:var(--navy);font-weight:700}
.ac-dd-empty{padding:12px;color:var(--muted);font-size:12px;text-align:center}
.bgrp{display:flex;gap:8px;margin-left:auto;align-items:flex-end;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;transition:filter .15s,transform .1s;white-space:nowrap}
.btn:hover{filter:brightness(1.08);transform:translateY(-1px)}
.btn-p{background:var(--teal);color:#fff}
.btn-g{background:var(--border);color:var(--text)}
/* KPIs */
.krow{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;padding:16px 28px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px}
.kpi.ka::before{background:linear-gradient(90deg,var(--eq-a),#7ab8e8)}
.kpi.kb::before{background:linear-gradient(90deg,var(--eq-b),#92cc5e)}
.kpi.kc::before{background:linear-gradient(90deg,var(--eq-c),#f5a55e)}
.kl{font-size:10.5px;font-weight:700;color:var(--text);letter-spacing:.3px;margin-bottom:4px}
.kv{font-size:17px;font-weight:700;letter-spacing:-.3px;line-height:1;margin:4px 0}
.kv.ok{color:var(--green)}
.kv.err{color:var(--red)}
.kf{font-size:10px;color:var(--teal);margin-top:3px;font-family:'Consolas',monospace;letter-spacing:0}
.ks{font-size:10.5px;color:var(--muted);margin-top:3px}
/* Tabela */
.tsec{padding:0 28px 32px}
.cnt-bar{padding:4px 28px 8px;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.tw{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}
table{width:100%;border-collapse:collapse}
thead tr.h1 th{background:var(--navy);color:#ffffff;padding:9px 10px;font-size:10.5px;font-weight:700;text-align:center;white-space:nowrap;letter-spacing:.3px;border-right:1px solid #2a4580}
thead tr.h1 th.left{text-align:left}
thead tr.h2 th{padding:6px 10px;font-size:10px;font-weight:600;text-align:right;white-space:nowrap;color:#ffffff;border-right:1px solid rgba(255,255,255,.1)}
thead tr.h2 th.left{text-align:left}
.ha{background:#1a3a6e}
.hb{background:#1e5c2e}
.hc{background:#7a3e10}
tr.ug-row td{background:var(--navy-mid);color:#ffffff;font-weight:700;padding:8px 10px;font-size:11.5px;cursor:pointer;white-space:nowrap}
tr.ug-row td:first-child{max-width:260px;overflow:hidden;text-overflow:ellipsis}
tr.ug-row:hover td{background:var(--navy-light)!important}
tr.ug-row .tog{margin-right:6px;display:inline-block;width:12px}
tr.ne-row td{padding:7px 10px;border-bottom:1px solid var(--border);font-size:12px;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.ne-row:nth-child(even) td{background:var(--row-alt)}
tr.ne-row:hover td{background:var(--hover)!important}
tr.ne-row.hidden{display:none}
td.right{text-align:right}
td.mono{font-family:'Consolas','Courier New',monospace;font-size:11.5px}
.ca{border-left:3px solid var(--eq-a)}
.cb{border-left:3px solid var(--eq-b)}
.cc{border-left:3px solid var(--eq-c)}
.cd{border-left:3px solid var(--eq-d)}
.ce{border-left:3px solid var(--eq-e)}
.vok{color:var(--green);font-weight:700}
.vneg{color:var(--red);font-weight:700}
.vzero{color:var(--muted)}
tfoot td{background:#e8f0f8;font-weight:700;border-top:2px solid var(--teal);padding:8px 10px;font-size:12px;white-space:nowrap;font-variant-numeric:tabular-nums}
.empty{text-align:center;padding:48px;color:var(--muted)}
/* Linhas de detalhe de conta */
tr.acct-row td{padding:5px 10px 5px 36px;border-bottom:1px solid #e8edf5;font-size:11.5px;background:#f5f8fd;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.acct-row td:first-child{max-width:150px;overflow:hidden;text-overflow:ellipsis}
tr.acct-row:last-of-type td{border-bottom:2px solid var(--border)}
tr.acct-row.hidden{display:none}
/* Toggle na NE */
.ne-tog{display:inline-block;width:14px;height:14px;line-height:14px;text-align:center;font-size:9px;background:var(--border);border-radius:3px;cursor:pointer;color:var(--muted);margin-right:6px;user-select:none;flex-shrink:0}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">🗂️</div>
    <h1>RPNP e RPP por Nota de Empenho<span>SIGGO · Ano Exercício 2026</span></h1>
  </div>
  <span id="ts">Atualizado em {timestamp}</span>
</header>
<div class="fbar">
  <div class="fg">
    <label>Gestão</label>
    <div class="ac-wrap">
      <input id="fg-input" class="ac-input" type="text" placeholder="Código ou nome…" autocomplete="off"
             oninput="onACInput('g')" onfocus="onACFocus('g')" onblur="onACBlur('g')">
      <button class="ac-clear" id="fg-clear" onclick="limparAC('g')" title="Limpar">✕</button>
      <div class="ac-dd" id="fg-dd"></div>
    </div>
  </div>
  <div class="fg">
    <label>Unidade Gestora</label>
    <div class="ac-wrap" style="min-width:280px">
      <input id="fu-input" class="ac-input" type="text" placeholder="Código ou nome…" autocomplete="off"
             oninput="onACInput('u')" onfocus="onACFocus('u')" onblur="onACBlur('u')">
      <button class="ac-clear" id="fu-clear" onclick="limparAC('u')" title="Limpar">✕</button>
      <div class="ac-dd" id="fu-dd"></div>
    </div>
  </div>
  <div class="fg">
    <label>Ano NE</label>
    <select id="fano" onchange="aplicar()"><option value="">Todos</option></select>
  </div>
  <div class="fg">
    <label>Exibir</label>
    <select id="fexib" onchange="aplicar()">
      <option value="dif">Apenas com diferença</option>
      <option value="">Todos os registros</option>
      <option value="ok">Apenas sem diferença</option>
    </select>
  </div>
  <div class="fg">
    <label>Buscar NE</label>
    <input id="busca" class="ac-input" type="text" placeholder="ex: 2024NE00459" oninput="aplicar()" style="min-width:190px;width:190px">
  </div>
  <div class="bgrp">
    <button class="btn btn-g" onclick="expandirTudo()">▼ Expandir tudo</button>
    <button class="btn btn-g" onclick="recolherTudo()">▲ Recolher tudo</button>
    <button class="btn btn-g" onclick="limpar()">↺ Limpar filtros</button>
    <button class="btn btn-p" onclick="exportar()">↓ Exportar CSV</button>
  </div>
</div>

<div class="krow" id="krow"></div>
<div class="cnt-bar"><span id="contagem"></span></div>

<div class="tsec">
  <div class="tw">
    <table>
      <thead>
        <tr class="h1">
          <th class="left" colspan="2">UG / Nota de Empenho</th>
          <th colspan="3" style="border-left:3px solid var(--eq-a)">A — Controles e Orçamentárias</th>
          <th colspan="3" style="border-left:3px solid var(--eq-b)">B — Retenções e Orçamentárias</th>
          <th colspan="3" style="border-left:3px solid var(--eq-c)">C — Principal</th>
        </tr>
        <tr class="h2">
          <th class="left ha" style="width:44px;min-width:44px">UG</th>
          <th class="left ha" style="width:106px;min-width:106px">NE</th>
          <th class="right ha ca" title="827110301 + 827110303">827110301<br>+ 827110303</th>
          <th class="right ha" title="632110300 + 632110400">632110300<br>+ 632110400</th>
          <th class="right ha" title="Diferença A">Dif. A</th>
          <th class="right hb cb" title="21881XXXX + 21882XXXX + 21883XXXX">21881XXXX<br>+21882XXXX<br>+21883XXXX</th>
          <th class="right hb" title="632110300 + 632110400 + 631810000">632110300<br>+632110400<br>+631810000</th>
          <th class="right hb" title="Diferença B">Dif. B</th>
          <th class="right hc cc" title="2XXXXXXXX exceto 2188XXXXX">Principal<br>2XXXXXXXX<br><span style="font-weight:400;font-size:9px;opacity:.75">(exc. 2188XXXXX)</span></th>
          <th class="right hc" title="632110100 + 631200000 + 631300000">632110100<br>+631200000<br>+631300000</th>
          <th class="right hc" title="Diferença C">Dif. C</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
      <tfoot id="tfoot"></tfoot>
    </table>
  </div>
</div>

<script>
const DADOS = {dados_json};
const DETALHE = {detalhe_json};
let fil = [];

const brl = v => {
  if (v === null || v === undefined || isNaN(v)) return '—';
  const r = Math.round(Number(v) * 100) / 100;
  return (r || 0).toLocaleString('pt-BR', {style:'currency', currency:'BRL'});
};
const vcDif = v => Math.abs(v) < 0.005 ? 'vok' : 'vneg';
const vcSaldo = v => Math.abs(v) < 0.005 ? 'vzero' : '';

// ── Autocomplete ────────────────────────────────────────────────────────────
function buildList(keyFn, labelFn) {
  const m = {};
  DADOS.forEach(r => { const k = keyFn(r); if (k && !m[k]) m[k] = labelFn(r); });
  return Object.entries(m).map(([c, label]) => ({c, label}))
               .sort((a, b) => a.c.localeCompare(b.c, 'pt-BR'));
}
DADOS.forEach(r => {
  r.GESTAO_LABEL = String(r.COGESTAO).padStart(5,'0') +
    (r.NOGESTAO && r.NOGESTAO !== 'Sem nome' ? ' - ' + r.NOGESTAO : '');
  r.UG_LABEL = String(r.COUG) +
    (r.NOUG && r.NOUG !== 'Sem nome' ? ' - ' + r.NOUG : '');
});
const gestaoList = buildList(r => String(r.COGESTAO).padStart(5,'0'), r => r.GESTAO_LABEL);
const ugList     = buildList(r => String(r.COUG),                     r => r.UG_LABEL);

const acState = {
  'g': {sel:'', list:gestaoList, inp:'fg-input', clr:'fg-clear', dd:'fg-dd'},
  'u': {sel:'', list:ugList,     inp:'fu-input', clr:'fu-clear', dd:'fu-dd'}
};

function onACInput(k) {
  const st = acState[k], v = document.getElementById(st.inp).value.toLowerCase();
  const m = v ? st.list.filter(x => x.c.includes(v) || x.label.toLowerCase().includes(v)) : st.list;
  renderACDD(k, m);
  if (!v) { st.sel = ''; document.getElementById(st.clr).style.display = 'none'; }
}
function onACFocus(k) {
  const st = acState[k], v = document.getElementById(st.inp).value.toLowerCase();
  renderACDD(k, v ? st.list.filter(x => x.c.includes(v) || x.label.toLowerCase().includes(v)) : st.list);
}
function onACBlur(k) { setTimeout(() => document.getElementById(acState[k].dd).style.display = 'none', 200); }
function renderACDD(k, lista) {
  const dd = document.getElementById(acState[k].dd);
  if (!lista.length) { dd.innerHTML = '<div class="ac-dd-empty">Nenhum resultado</div>'; dd.style.display = 'block'; return; }
  dd.innerHTML = lista.slice(0, 100).map(x => {
    const sep = x.label.indexOf(' - ');
    const h = sep >= 0 ? `<strong>${x.label.slice(0,sep)}</strong> —${x.label.slice(sep+2)}` : x.label;
    return `<div class="ac-dd-item" onmousedown="selAC('${k}','${x.c}','${x.label.replace(/\\/g,'\\\\').replace(/'/g,"\\'")}')">${h}</div>`;
  }).join('');
  dd.style.display = 'block';
}
function selAC(k, c, label) {
  const st = acState[k]; st.sel = c;
  document.getElementById(st.inp).value = label;
  document.getElementById(st.dd).style.display = 'none';
  document.getElementById(st.clr).style.display = 'block';
  aplicar();
}
function limparAC(k) {
  const st = acState[k]; st.sel = '';
  document.getElementById(st.inp).value = '';
  document.getElementById(st.clr).style.display = 'none';
  aplicar();
}

// ── Filtros e anos ──────────────────────────────────────────────────────────
(function populaAnos() {
  const anos = [...new Set(DADOS.map(r => r.NE ? r.NE.substring(0,4) : ''))].filter(Boolean).sort();
  const sfano = document.getElementById('fano');
  anos.forEach(a => sfano.add(new Option(a, a)));
})();

function hasDif(r) {
  return Math.abs(r.DIF_C) >= 0.005 || Math.abs(r.DIF_D) >= 0.005 || Math.abs(r.DIF_E) >= 0.005;
}

function aplicar() {
  const fano  = document.getElementById('fano').value;
  const fexib = document.getElementById('fexib').value;
  const busca = document.getElementById('busca').value.trim().toUpperCase();

  fil = DADOS.filter(r => {
    if (acState['g'].sel && String(r.COGESTAO).padStart(5,'0') !== acState['g'].sel) return false;
    if (acState['u'].sel && String(r.COUG) !== acState['u'].sel) return false;
    if (fano  && r.NE && r.NE.substring(0,4) !== fano) return false;
    if (busca && !(r.NE || '').toUpperCase().includes(busca)) return false;
    if (fexib === 'dif' && !hasDif(r)) return false;
    if (fexib === 'ok'  &&  hasDif(r)) return false;
    return true;
  });

  render();
  kpis();
}

// ── Render ──────────────────────────────────────────────────────────────────
// Retorna qual "slot" de coluna a conta ocupa no detalhe
// Equação A: 827110301+827110303 = 632110300+632110400
// Equação B: 21881+21882+21883 = 632110300+632110400+631810000
// Equação C: 2XXXXXXXX principal = 632110100+631200000+631300000
function classifConta(c) {
  if (c === 827110301 || c === 827110303)              return 'a1'; // A esq: contas de controle 827
  if (c === 632110300 || c === 632110400)              return 'a2'; // A dir / B dir: orçamentárias retenção
  if (c >= 218810000 && c <= 218819999)                return 'b1'; // B esq: 21881XXXX
  if (c >= 218830000 && c <= 218839999)                return 'b1'; // B esq: 21883XXXX
  if (c >= 218820000 && c <= 218829999)                return 'b1'; // B esq: 21882XXXX
  if (c === 631810000)                                 return 'b2'; // B dir extra: 631810000
  if (c >= 200000000 && c <= 299999999)                return 'c1'; // C esq: principal 2XXXXXXXX
  if (c === 632110100 || c === 631200000 || c === 631300000) return 'c2'; // C dir
  return 'c1';
}

function render() {
  const tbody = document.getElementById('tbody');
  const tfoot = document.getElementById('tfoot');
  const nNE = fil.length;
  const nUG = new Set(fil.map(r => r.COUG)).size;
  document.getElementById('contagem').textContent = nNE + ' NE(s) em ' + nUG + ' UG(s)';

  if (!nNE) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty">Nenhum registro encontrado</td></tr>';
    tfoot.innerHTML = '';
    return;
  }

  const porUG = {};
  fil.forEach(r => {
    const k = r.COUG;
    if (!porUG[k]) porUG[k] = {noug:r.NOUG, gest:r.COGESTAO, nogest:r.NOGESTAO, nes:[]};
    porUG[k].nes.push(r);
  });

  let rows = '';
  let tot = {s827T:0,s632R:0,difA:0,s2188T:0,s632D:0,difB:0,s2:0,s632E:0,difC:0};

  Object.entries(porUG).forEach(([ug, g]) => {
    const uid   = 'ug_' + ug;
    const t827T  = g.nes.reduce((s,r) => s + r.S_827110301 + r.S_827110303, 0);
    const t632R  = g.nes.reduce((s,r) => s + r.S_632110300 + r.S_632110400, 0);
    const tDifA  = g.nes.reduce((s,r) => s + r.DIF_C, 0);
    const t2188T = g.nes.reduce((s,r) => s + r.S_21881 + r.S_21882 + r.S_21883, 0);
    const t632D  = g.nes.reduce((s,r) => s + r.S_632110300 + r.S_632110400 + r.S_631810000, 0);
    const tDifB  = g.nes.reduce((s,r) => s + r.DIF_D, 0);
    const tS2    = g.nes.reduce((s,r) => s + r.S_2, 0);
    const t632E  = g.nes.reduce((s,r) => s + r.S_632110100 + r.S_631200000 + r.S_631300000, 0);
    const tDifC  = g.nes.reduce((s,r) => s + r.DIF_E, 0);

    tot.s827T+=t827T; tot.s632R+=t632R; tot.difA+=tDifA;
    tot.s2188T+=t2188T; tot.s632D+=t632D; tot.difB+=tDifB;
    tot.s2+=tS2; tot.s632E+=t632E; tot.difC+=tDifC;

    const ugLabel = String(ug) + ' · ' + g.noug;

    rows += `<tr class="ug-row" onclick="toggleUG('${uid}')">
      <td colspan="2"><span class="tog" id="tog_${uid}">▼</span>${ugLabel}
        <span style="font-size:10px;opacity:.65;margin-left:8px">(${g.nes.length} NE)</span></td>
      <td class="right ca">${brl(t827T)}</td>
      <td class="right">${brl(t632R)}</td>
      <td class="right ${vcDif(tDifA)}">${brl(tDifA)}</td>
      <td class="right cb">${brl(t2188T)}</td>
      <td class="right">${brl(t632D)}</td>
      <td class="right ${vcDif(tDifB)}">${brl(tDifB)}</td>
      <td class="right cc">${brl(tS2)}</td>
      <td class="right">${brl(t632E)}</td>
      <td class="right ${vcDif(tDifC)}">${brl(tDifC)}</td>
    </tr>`;

    g.nes.forEach(r => {
      const s827T  = r.S_827110301 + r.S_827110303;
      const s632R  = r.S_632110300 + r.S_632110400;
      const s2188T = r.S_21881 + r.S_21882 + r.S_21883;
      const s632D  = r.S_632110300 + r.S_632110400 + r.S_631810000;
      const s632E  = r.S_632110100 + r.S_631200000 + r.S_631300000;
      const neKey  = String(r.COUG) + '|' + r.NE;
      const eps = 0.005;
      const difA = Math.abs(r.DIF_C) >= eps;  // A = antigo C
      const difB = Math.abs(r.DIF_D) >= eps;  // B = antigo D
      const difC = Math.abs(r.DIF_E) >= eps;  // C = antigo E
      const contas = (DETALHE[neKey] || []).filter(ct => {
        const g = classifConta(ct.c);
        if (g === 'a1') return difA;
        if (g === 'a2') return difA || difB;  // 632 aparece em A e B
        if (g === 'b1') return difB;
        if (g === 'b2') return difB;          // 631810000
        if (g === 'c1') return difC;
        if (g === 'c2') return difC;
        return true;
      });
      const neid = uid + '_' + (r.NE || 'x').replace(/[^a-zA-Z0-9]/g, '_');
      const temDet = contas.length > 0;

      rows += `<tr class="ne-row" data-ug="${uid}">
        <td class="mono" colspan="2" style="padding-left:28px">
          ${temDet ? `<span class="ne-tog" id="netog_${neid}" onclick="event.stopPropagation();toggleNE('${neid}')">▶</span>` : '<span style="display:inline-block;width:20px"></span>'}${r.NE || '—'}
        </td>
        <td class="right ca ${vcSaldo(s827T)}">${brl(s827T)}</td>
        <td class="right ${vcSaldo(s632R)}">${brl(s632R)}</td>
        <td class="right ${vcDif(r.DIF_C)}">${brl(r.DIF_C)}</td>
        <td class="right cb ${vcSaldo(s2188T)}">${brl(s2188T)}</td>
        <td class="right ${vcSaldo(s632D)}">${brl(s632D)}</td>
        <td class="right ${vcDif(r.DIF_D)}">${brl(r.DIF_D)}</td>
        <td class="right cc ${vcSaldo(r.S_2)}">${brl(r.S_2)}</td>
        <td class="right ${vcSaldo(s632E)}">${brl(s632E)}</td>
        <td class="right ${vcDif(r.DIF_E)}">${brl(r.DIF_E)}</td>
      </tr>`;

      contas.forEach(ct => {
        const grp = classifConta(ct.c);
        const dash = '<td class="right" style="color:var(--muted)">—</td>';
        const val  = (cls) => `<td class="right ${cls}" style="font-weight:600">${brl(ct.s)}</td>`;
        const cA1  = grp==='a1' ? val('ca') : `<td class="right ca" style="color:var(--muted)">—</td>`;
        const cA2  = grp==='a2' ? val('') : dash;
        const cB1  = grp==='b1' ? val('cb') : `<td class="right cb" style="color:var(--muted)">—</td>`;
        const cB2  = grp==='b2' ? val('') : dash;
        const cC1  = grp==='c1' ? val('cc') : `<td class="right cc" style="color:var(--muted)">—</td>`;
        const cC2  = grp==='c2' ? val('') : dash;
        const label = `${ct.c} · ${ct.n}`;
        rows += `<tr class="acct-row hidden" data-ne="${neid}" data-ug="${uid}">
          <td colspan="2" title="${ct.n}" style="padding-left:44px;color:var(--muted);font-size:11px;font-family:'Consolas',monospace;white-space:nowrap;max-width:280px;overflow:hidden;text-overflow:ellipsis">${label}</td>
          ${cA1}${cA2}${dash}${cB1}${cB2}${dash}${cC1}${cC2}${dash}
        </tr>`;
      });
    });
  });

  tbody.innerHTML = rows;
  tfoot.innerHTML = `<tr>
    <td colspan="2">TOTAL GERAL (${nNE} NE / ${nUG} UGs)</td>
    <td class="right ca">${brl(tot.s827T)}</td><td class="right">${brl(tot.s632R)}</td>
    <td class="right ${vcDif(tot.difA)}">${brl(tot.difA)}</td>
    <td class="right cb">${brl(tot.s2188T)}</td><td class="right">${brl(tot.s632D)}</td>
    <td class="right ${vcDif(tot.difB)}">${brl(tot.difB)}</td>
    <td class="right cc">${brl(tot.s2)}</td><td class="right">${brl(tot.s632E)}</td>
    <td class="right ${vcDif(tot.difC)}">${brl(tot.difC)}</td>
  </tr>`;
}

function toggleNE(neid) {
  const tog = document.getElementById('netog_' + neid);
  const rows = document.querySelectorAll('tr.acct-row[data-ne="' + neid + '"]');
  const open = tog.textContent === '▶';
  tog.textContent = open ? '▼' : '▶';
  tog.style.background = open ? 'var(--teal)' : 'var(--border)';
  tog.style.color = open ? '#fff' : 'var(--muted)';
  rows.forEach(r => r.classList.toggle('hidden', !open));
}

function toggleUG(uid) {
  const tog = document.getElementById('tog_' + uid);
  const neRows = document.querySelectorAll('tr.ne-row[data-ug="' + uid + '"]');
  const acctRows = document.querySelectorAll('tr.acct-row[data-ug="' + uid + '"]');
  const collapsed = tog.closest('tr').classList.toggle('collapsed');
  tog.textContent = collapsed ? '▶' : '▼';
  neRows.forEach(r => r.classList.toggle('hidden', collapsed));
  if (collapsed) acctRows.forEach(r => r.classList.add('hidden'));
}
function expandirTudo() {
  document.querySelectorAll('tr.ne-row').forEach(r => r.classList.remove('hidden'));
  document.querySelectorAll('.tog').forEach(el => el.textContent = '▼');
  document.querySelectorAll('tr.ug-row').forEach(r => r.classList.remove('collapsed'));
  // Acct-rows permanecem no estado individual; não força abertura
}
function recolherTudo() {
  document.querySelectorAll('tr.ne-row,tr.acct-row').forEach(r => r.classList.add('hidden'));
  document.querySelectorAll('.tog').forEach(el => el.textContent = '▶');
  document.querySelectorAll('tr.ug-row').forEach(r => r.classList.add('collapsed'));
  document.querySelectorAll('.ne-tog').forEach(el => {
    el.textContent = '▶';
    el.style.background = 'var(--border)';
    el.style.color = 'var(--muted)';
  });
}

// ── KPIs ────────────────────────────────────────────────────────────────────
function kpis() {
  const totDifA = fil.reduce((s,r) => s + r.DIF_C, 0);  // A = antigo C
  const totDifB = fil.reduce((s,r) => s + r.DIF_D, 0);  // B = antigo D
  const totDifC = fil.reduce((s,r) => s + r.DIF_E, 0);  // C = antigo E
  const neDifA  = fil.filter(r => Math.abs(r.DIF_C) >= 0.005).length;
  const neDifB  = fil.filter(r => Math.abs(r.DIF_D) >= 0.005).length;
  const neDifC  = fil.filter(r => Math.abs(r.DIF_E) >= 0.005).length;

  document.getElementById('krow').innerHTML = `
    <div class="kpi ka">
      <div class="kl">A — Controles e Orçamentárias</div>
      <div class="kv ${Math.abs(totDifA)<0.005?'ok':'err'}">${brl(totDifA)}</div>
      <div class="kf">827110301 + 827110303 = 632110300 + 632110400</div>
      <div class="ks">${neDifA} NE(s) com divergência</div>
    </div>
    <div class="kpi kb">
      <div class="kl">B — Retenções e Orçamentárias</div>
      <div class="kv ${Math.abs(totDifB)<0.005?'ok':'err'}">${brl(totDifB)}</div>
      <div class="kf">21881XXXX + 21882XXXX + 21883XXXX = 632110300 + 632110400 + 631810000</div>
      <div class="ks">${neDifB} NE(s) com divergência</div>
    </div>
    <div class="kpi kc">
      <div class="kl">C — Principal</div>
      <div class="kv ${Math.abs(totDifC)<0.005?'ok':'err'}">${brl(totDifC)}</div>
      <div class="kf">2XXXXXXXX (exc. 2188XXXXX) = 632110100 + 631200000 + 631300000</div>
      <div class="ks">${neDifC} NE(s) com divergência</div>
    </div>`;
}

// ── Limpar / Exportar ───────────────────────────────────────────────────────
function limpar() {
  limparAC('g'); limparAC('u');
  document.getElementById('fano').value = '';
  document.getElementById('fexib').value = 'dif';
  document.getElementById('busca').value = '';
  aplicar();
}

function exportar() {
  if (!fil.length) return alert('Nenhum dado para exportar.');
  const hdrs = ['UG','Nome UG','Gestao','Nome Gestao','NE',
    '827110301 + 827110303','632110300 + 632110400','Dif A',
    '21881+21882+21883XXXX','632110300+632110400+631810000','Dif B',
    '2XXXXXXXX principal','632110100+631200000+631300000','Dif C'];
  const cel = v => {
    if (typeof v === 'number') return String(v).replace('.', ',');
    const s = (v ?? '').toString().trim();
    return /^\d+$/.test(s) ? `="${s}"` : s;
  };
  const linhas = [hdrs.join(';')];
  fil.forEach(r => {
    linhas.push([
      r.COUG, r.NOUG, String(r.COGESTAO).padStart(5,'0'), r.NOGESTAO, r.NE,
      r.S_827110301+r.S_827110303, r.S_632110300+r.S_632110400, r.DIF_C,
      r.S_21881+r.S_21882+r.S_21883, r.S_632110300+r.S_632110400+r.S_631810000, r.DIF_D,
      r.S_2, r.S_632110100+r.S_631200000+r.S_631300000, r.DIF_E
    ].map(cel).join(';'));
  });
  const blob = new Blob(['\uFEFF' + linhas.join('\n')], {type:'text/csv;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'controle_ne_anteriores.csv';
  a.click();
}

aplicar();
</script>
</body>
</html>
"""


def _normalizar(df: pd.DataFrame) -> list:
    registros = []
    for _, row in df.iterrows():
        r = {}
        for col in df.columns:
            v = row[col]
            if hasattr(v, 'item'):
                v = v.item()
            r[col] = None if (isinstance(v, float) and str(v) == 'nan') else v
        registros.append(r)
    return registros


def gerar_html(dados: list, detalhe: dict) -> str:
    html = HTML_TEMPLATE
    html = html.replace('{dados_json}', json.dumps(dados, ensure_ascii=False))
    html = html.replace('{detalhe_json}', json.dumps(detalhe, ensure_ascii=False))
    html = html.replace('{timestamp}', datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    return html


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ug', default='', help='Filtrar por UG específica')
    args = parser.parse_args()

    filtro_ug = f"AND s.COUG = '{args.ug}'" if args.ug else ''

    sql        = SQL.replace('{schema}', SCHEMA).replace('{filtro_ug}', filtro_ug)
    sql_det    = SQL_DETALHE.replace('{schema}', SCHEMA).replace('{filtro_ug}', filtro_ug)

    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle...")
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN)

    print(f"[{datetime.now():%H:%M:%S}] Executando consulta principal...")
    df = pd.read_sql(sql, conn)

    print(f"[{datetime.now():%H:%M:%S}] Executando consulta de detalhe por conta...")
    df_det = pd.read_sql(sql_det, conn)
    conn.close()

    total = len(df)
    com_dif = ((df['DIF_C'].abs() >= 0.005) | (df['DIF_D'].abs() >= 0.005) |
               (df['DIF_E'].abs() >= 0.005)).sum()
    print(f"[{datetime.now():%H:%M:%S}] {total} NE(s) retornadas | {com_dif} com diferença.")
    print(f"[{datetime.now():%H:%M:%S}] {len(df_det)} linhas de detalhe por conta.")

    if total == 0:
        print("Nenhum registro encontrado.")
        sys.exit(0)

    dados = _normalizar(df)

    # Monta dict detalhe: chave "COUG|NE" → lista de {c, n, s}
    detalhe: dict = {}
    for _, row in df_det.iterrows():
        ug  = row['COUG']
        ne  = row['NE']
        key = f"{ug}|{ne}"
        v   = row['SALDO']
        if hasattr(v, 'item'): v = v.item()
        if isinstance(v, float) and str(v) == 'nan': v = 0.0
        c_val = row['COCONTACONTABIL']
        if hasattr(c_val, 'item'): c_val = c_val.item()
        n_val = row['NOCONTACONTABIL']
        if not isinstance(n_val, str): n_val = str(n_val) if n_val else 'Sem nome'
        detalhe.setdefault(key, []).append({'c': int(c_val), 'n': n_val, 's': round(float(v), 2)})

    html = gerar_html(dados, detalhe)

    saida = Path(__file__).parent / ARQUIVO_HTML
    saida.write_text(html, encoding='utf-8')
    print(f"[{datetime.now():%H:%M:%S}] HTML salvo: {saida.name} ({saida.stat().st_size / 1024:.0f} KB)")
    print(f"  -> Abra o arquivo localmente para visualizar.")


if __name__ == '__main__':
    main()
