"""
DDR por Lancamento — SIGGO/Oracle
Compara movimentos (lancamentos) das contas orcamentarias (6XX/7XX) com as
contas de controle (8XX) correspondentes, por par de contas/documento/evento/fonte,
conforme MCASP 11a edicao. Gera HTML autocontido e publica no GitHub Pages.

Uso:
    python extrair_ddr_lancamento.py
    python extrair_ddr_lancamento.py --ug 10101
    python extrair_ddr_lancamento.py --no-push
"""

import argparse
import base64
import gzip
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import oracledb
import pandas as pd

# ── Conexao Oracle ─────────────────────────────────────────────────────────────
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ORACLE_USER = os.environ["ORACLE_USER"]
ORACLE_PASS = os.environ["ORACLE_PASS"]
ORACLE_DSN  = os.environ["ORACLE_DSN"]
SCHEMA      = "MIL2026."

# ── GitHub ─────────────────────────────────────────────────────────────────────
GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
GITHUB_USER   = os.environ["GITHUB_USER"]
GITHUB_REPO   = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = os.environ["GITHUB_BRANCH"]
ARQUIVO_HTML  = "ddr_lancamento.html"

# ── SQL ────────────────────────────────────────────────────────────────────────
# Correlacao (MCASP 11a edicao):
#   622920101 = 821120100
#   622920102 = 821120200
#   622920103 = 821130200 + 821130100
#   622920104 = 821140000
#   827110401 = 821130300
#   721190300 = 821110100 + 821110200 + 821120100
#
# Regra de sinal:
#   Credoras (6XXXXXXXX, 8XXXXXXXX, 827110401): C aumenta, D diminui
#   Devedora  (721190300):                       D aumenta, C diminui
#
# Fonte (a):
#   62292010X: COCONTACORRENTE[1..11] = NUNE -> JOIN NOTAEMPENHO(COGESTAO_EMIT,COUG_EMIT,NUNE).COFONTE
#   721190300 e 827110401: TO_NUMBER(SUBSTR(COCONTACORRENTE,1,9))
# Fonte (b):
#   8211XXXXX: TO_NUMBER(SUBSTR(COCONTACORRENTE,1,9))
#
# UG Contábil vs. Emitente:
#   COUGCONTAB / COGESTAOCONTAB: UG/Gestao onde a conta contabil e sensibilizada (exibida na linha)
#   COUG / COGESTAO: UG/Gestao emitente do documento (chave do documento; exibida em coluna separada)
#   Chave do documento: COUG_EMIT + COGESTAO_EMIT + NUDOCUMENTO (o documento e emitido por uma UG apenas)
SQL = """
WITH lb AS (
    SELECT
        l.COGESTAO       AS COGESTAO_EMIT,
        l.COUG           AS COUG_EMIT,
        l.COGESTAOCONTAB AS COGESTAO,
        l.COUGCONTAB     AS COUG,
        l.INMES,
        l.NUDOCUMENTO,
        l.COCONTACONTABIL,
        l.COEVENTO,
        l.COCONTACORRENTE,
        l.INDEBITOCREDITO,
        SUM(l.VALANCAMENTO) AS VALANCAMENTO,
        MIN(l.DALANCAMENTO) AS DALANCAMENTO
    FROM {schema}VLANCAMENTOCONTABIL l
    WHERE (l.COCONTACONTABIL IN (
        622920101, 622920102, 622920103, 622920104,
        721190300, 827110401,
        821120100, 821120200, 821130200, 821130100, 821140000,
        821130300, 821110100, 821110200,
        -- Restos a Pagar (63XXXXXXX)
        631100000, 631810000, 631200000, 631820000,
        631300000, 632110100, 632110200, 632110300, 632110400, 631400000
    ) OR (l.COCONTACONTABIL BETWEEN 632210000 AND 632219999))
    {filtro_ug}
    GROUP BY
        l.COGESTAO, l.COUG, l.COGESTAOCONTAB, l.COUGCONTAB,
        l.INMES, l.NUDOCUMENTO, l.COCONTACONTABIL, l.COEVENTO,
        l.COCONTACORRENTE, l.INDEBITOCREDITO
),
por_conta AS (
    SELECT
        COGESTAO_EMIT, COUG_EMIT, COGESTAO, COUG,
        INMES, NUDOCUMENTO, COCONTACONTABIL, COEVENTO, COCONTACORRENTE,
        MIN(DALANCAMENTO) AS DALANCAMENTO,
        ROUND(SUM(
            CASE
                WHEN COCONTACONTABIL IN (622920101,622920102,622920103,622920104,827110401,
                    631100000,631810000,631200000,631820000,
                    631300000,632110100,632110200,632110300,632110400,
                    631400000,632210000,632211000,632212000,632213000,632214000,
                    632215000,632216000,632217000,632218000,632219000)
                     THEN CASE INDEBITOCREDITO WHEN 'C' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                WHEN COCONTACONTABIL = 721190300
                     THEN CASE INDEBITOCREDITO WHEN 'D' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                ELSE CASE INDEBITOCREDITO WHEN 'C' THEN VALANCAMENTO ELSE -VALANCAMENTO END
            END
        ), 2) AS VLNET
    FROM lb
    GROUP BY COGESTAO_EMIT, COUG_EMIT, COGESTAO, COUG,
             INMES, NUDOCUMENTO, COCONTACONTABIL, COEVENTO, COCONTACORRENTE
),
orc AS (
    SELECT
        pc.COGESTAO_EMIT, pc.COUG_EMIT, pc.COGESTAO, pc.COUG,
        pc.INMES, pc.NUDOCUMENTO, pc.COEVENTO, pc.DALANCAMENTO,
        pc.COCONTACONTABIL AS CONTA_A,
        CASE pc.COCONTACONTABIL
            WHEN 622920101 THEN '821120100'
            WHEN 622920102 THEN '821120200'
            WHEN 622920103 THEN '821130200 + 821130100'
            WHEN 622920104 THEN '821140000'
            WHEN 827110401 THEN '821130300'
            WHEN 721190300 THEN '821110100 + 821110200 + 821120100'
            -- Restos a Pagar (63XXXXXXX)
            WHEN 631100000 THEN '821120100'
            WHEN 631810000 THEN '821120100'
            WHEN 631200000 THEN '821120200'
            WHEN 631820000 THEN '821120200'
            WHEN 631300000 THEN '821130200 + 821130100'
            WHEN 632110100 THEN '821130200 + 821130100'
            WHEN 632110200 THEN '821130200 + 821130100'
            WHEN 632110300 THEN '821130200 + 821130100'
            WHEN 632110400 THEN '821130200 + 821130100'
            WHEN 631400000 THEN '821140000'
            ELSE CASE WHEN pc.COCONTACONTABIL BETWEEN 632210000 AND 632219999 THEN '821140000' END
        END AS CONTA_B_REF,
        CASE
            WHEN pc.COCONTACONTABIL IN (622920101,622920102,622920103,622920104)
                THEN ne.COFONTE
            WHEN pc.COCONTACONTABIL IN (631100000,631810000,631200000,631820000,
                631300000,632110100,632110200,632110300,632110400,631400000)
              OR (pc.COCONTACONTABIL BETWEEN 632210000 AND 632219999)
                THEN nerp.COFONTE
            ELSE TO_NUMBER(SUBSTR(pc.COCONTACORRENTE, 1, 9))
        END AS FONTE_A,
        pc.VLNET AS MOV_A
    FROM por_conta pc
    LEFT JOIN {schema}NOTAEMPENHO ne
        ON  pc.COCONTACONTABIL IN (622920101,622920102,622920103,622920104)
        AND ne.NUNE        = SUBSTR(pc.COCONTACORRENTE, 1, 11)
        AND ne.COGESTAO    = pc.COGESTAO_EMIT
        AND ne.COUG        = pc.COUG_EMIT
    LEFT JOIN {schema}NERESTOPAGAR nerp
        ON  (pc.COCONTACONTABIL IN (631100000,631810000,631200000,631820000,
                631300000,632110100,632110200,632110300,632110400,631400000)
             OR (pc.COCONTACONTABIL BETWEEN 632210000 AND 632219999))
        AND nerp.NUNE      = SUBSTR(pc.COCONTACORRENTE, 1, 11)
        AND nerp.COGESTAO  = pc.COGESTAO_EMIT
        AND nerp.COUG      = pc.COUG_EMIT
    WHERE pc.COCONTACONTABIL IN (622920101,622920102,622920103,622920104,721190300,827110401,
        631100000,631810000,631200000,631820000,
        631300000,632110100,632110200,632110300,632110400,631400000)
       OR (pc.COCONTACONTABIL BETWEEN 632210000 AND 632219999)
),
ctl AS (
    -- Contas de controle; CONTA_B_REAL = conta individual sensibilizada (ex: 821130200)
    SELECT
        COGESTAO_EMIT, COUG_EMIT, COGESTAO, COUG,
        INMES, NUDOCUMENTO, COEVENTO, DALANCAMENTO,
        COCONTACONTABIL AS CONTA_B_REAL,
        CASE COCONTACONTABIL
            WHEN 821120100 THEN 622920101
            WHEN 821120200 THEN 622920102
            WHEN 821130200 THEN 622920103
            WHEN 821130100 THEN 622920103
            WHEN 821140000 THEN 622920104
            WHEN 821130300 THEN 827110401
            WHEN 821110100 THEN 721190300
            WHEN 821110200 THEN 721190300
        END AS CONTA_KEY,
        TO_NUMBER(SUBSTR(COCONTACORRENTE, 1, 9)) AS FONTE_B,
        VLNET AS MOV_B
    FROM por_conta
    WHERE COCONTACONTABIL IN (821120100,821120200,821130200,821130100,
                               821140000,821130300,821110100,821110200)
    UNION ALL
    -- 821120100 tambem integra o par de 721190300 (MCASP 11a ed.)
    SELECT
        COGESTAO_EMIT, COUG_EMIT, COGESTAO, COUG,
        INMES, NUDOCUMENTO, COEVENTO, DALANCAMENTO,
        821120100 AS CONTA_B_REAL,
        721190300 AS CONTA_KEY,
        TO_NUMBER(SUBSTR(COCONTACORRENTE, 1, 9)) AS FONTE_B,
        VLNET AS MOV_B
    FROM por_conta
    WHERE COCONTACONTABIL = 821120100
),
ctl_agg AS (
    -- Agrega por (emitente, documento, evento, conta_key, conta_b_real, fonte_b)
    SELECT
        COGESTAO_EMIT, COUG_EMIT, COGESTAO, COUG,
        INMES, NUDOCUMENTO, COEVENTO,
        CONTA_KEY, CONTA_B_REAL, FONTE_B,
        SUM(MOV_B)        AS MOV_B,
        MIN(DALANCAMENTO) AS DALANCAMENTO
    FROM ctl
    GROUP BY COGESTAO_EMIT, COUG_EMIT, COGESTAO, COUG,
             INMES, NUDOCUMENTO, COEVENTO, CONTA_KEY, CONTA_B_REAL, FONTE_B
)
SELECT
    COALESCE(o.COGESTAO,      c.COGESTAO)                             AS COGESTAO,
    COALESCE(o.COUG,          c.COUG)                                 AS COUG,
    COALESCE(o.INMES,         c.INMES)                                AS INMES,
    COALESCE(o.COEVENTO,      c.COEVENTO)                             AS COEVENTO,
    TO_CHAR(COALESCE(o.DALANCAMENTO, c.DALANCAMENTO), 'DD/MM/YYYY')   AS DALANCAMENTO,
    COALESCE(o.COGESTAO_EMIT, c.COGESTAO_EMIT)                        AS COGESTAO_EMIT,
    COALESCE(o.COUG_EMIT,     c.COUG_EMIT)                            AS COUG_EMIT,
    COALESCE(o.NUDOCUMENTO,   c.NUDOCUMENTO)                          AS NUDOCUMENTO,
    COALESCE(o.CONTA_A,       c.CONTA_KEY)                            AS CONTA_A,
    o.FONTE_A                                                         AS FONTE_A,
    NVL(o.MOV_A, 0)                                                   AS MOV_A,
    c.CONTA_B_REAL                                                    AS CONTA_B,
    c.FONTE_B                                                         AS FONTE_B,
    NVL(c.MOV_B, 0)                                                   AS MOV_B,
    NVL(o.MOV_A, 0) - NVL(c.MOV_B, 0)                                AS DIFERENCA
FROM orc o
FULL OUTER JOIN ctl_agg c
    ON  o.COGESTAO_EMIT = c.COGESTAO_EMIT
    AND o.COUG_EMIT     = c.COUG_EMIT
    AND o.COGESTAO      = c.COGESTAO
    AND o.COUG          = c.COUG
    AND o.INMES         = c.INMES
    AND o.NUDOCUMENTO   = c.NUDOCUMENTO
    AND o.COEVENTO      = c.COEVENTO
    AND o.CONTA_A       = c.CONTA_KEY
    AND (o.FONTE_A      = c.FONTE_B OR (o.FONTE_A IS NULL AND c.FONTE_B IS NULL))
ORDER BY
    COALESCE(o.DALANCAMENTO, c.DALANCAMENTO),
    COALESCE(o.COGESTAO, c.COGESTAO),
    COALESCE(o.COUG, c.COUG),
    COALESCE(o.COGESTAO_EMIT, c.COGESTAO_EMIT),
    COALESCE(o.COUG_EMIT, c.COUG_EMIT),
    COALESCE(o.NUDOCUMENTO, c.NUDOCUMENTO),
    COALESCE(o.CONTA_A, c.CONTA_KEY),
    c.CONTA_B_REAL
"""

# ── HTML Template ──────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<!-- v-ddr-lancamento-8 -->
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DDR por Lançamento — SIGGO</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --navy:#0d1b3e;--navy-light:#1e3267;
  --teal:#0090a8;--teal-light:#00b8d4;
  --surface:#fff;--bg:#f2f5f9;--border:#dce3ed;
  --row-alt:#f4f7fb;--hover:#e8f0f8;
  --text:#1a2033;--muted:#6b7a99;
  --red:#c0392b;--green:#1a7a44;--radius:10px;
  --shadow:0 2px 12px rgba(13,27,62,.10);
  --blue-btn:#487AA8;
}
.btn-b{background:var(--blue-btn);color:#fff}
.ev-drop{position:relative}
.ev-cnt-badge{background:rgba(255,255,255,.22);border-radius:10px;padding:1px 7px;font-size:10px;margin-left:5px;font-weight:700}
.ev-popup{position:absolute;right:0;top:calc(100% + 8px);z-index:400;width:460px;background:#fff;border:1.5px solid var(--border);border-radius:var(--radius);box-shadow:0 8px 28px rgba(0,0,0,.18);display:none;overflow:hidden}
.ev-popup.open{display:block}
.ev-popup-hdr{padding:10px 16px;font-weight:700;font-size:12px;color:var(--navy);border-bottom:1px solid var(--border);background:#f0f4fb;display:flex;justify-content:space-between;align-items:center}
.ev-popup-hdr span{color:var(--muted);font-weight:400;font-size:11px}
.ev-popup-body{max-height:400px;overflow-y:auto;overflow-x:hidden}
.ev-popup-foot{padding:8px 12px;border-top:1px solid var(--border);display:flex;justify-content:flex-end;background:#fafbfd}
.ev-tbl{width:100%;border-collapse:collapse}
.ev-tbl th{background:#f0f4fb;color:var(--muted);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;padding:6px 7px;text-align:right;border-bottom:1px solid var(--border)}
.ev-tbl th:first-child{text-align:left}
.ev-tbl td{padding:6px 7px;border-bottom:1px solid var(--border);font-size:11.5px;text-align:right;font-variant-numeric:tabular-nums}
.ev-tbl td:first-child{font-family:'Consolas','Courier New',monospace;font-size:11.5px;font-weight:700;color:var(--navy);text-align:left}
.ev-tbl tr:last-child td{border-bottom:none}
.ev-tbl tr:hover td{background:var(--hover)}
body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}
header{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}
.hlogo{width:32px;height:32px;background:var(--teal);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}
header h1{font-size:14px;font-weight:700;letter-spacing:.6px}
header h1 span{font-weight:400;color:#9ab0cc;font-size:12px;display:block;letter-spacing:0;margin-top:1px}
.voltar{font-size:11px;color:#7a99bb;text-decoration:none;display:flex;align-items:center;gap:4px;margin-left:20px;opacity:.8}
.voltar:hover{opacity:1}
#ts{font-size:11px;color:#7a99bb;white-space:nowrap}
.fbar{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
.fg{display:flex;flex-direction:column;gap:4px}
.fg label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.fg select,.fg input[type=text]{border:1.5px solid var(--border);border-radius:6px;padding:7px 10px;font-size:12.5px;background:#fff;color:var(--text)}
.fg select{padding-right:28px;min-width:130px;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 9px center;appearance:none}
.fg select:focus,.fg input[type=text]:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.fg input[type=text]{min-width:200px}
.ug-wrap{position:relative;min-width:200px}
.ug-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text)}
.ug-input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ug-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}
.ug-dd{position:absolute;top:calc(100% + 4px);left:0;right:0;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:220px;overflow-y:auto;display:none}
.ug-dd-item{padding:7px 12px;cursor:pointer;font-size:12px;border-bottom:1px solid var(--border)}
.ug-dd-item:last-child{border-bottom:none}
.ug-dd-item:hover{background:var(--hover)}
.ug-dd-item strong{color:var(--navy);font-weight:700}
.ug-dd-empty{padding:12px;color:var(--muted);font-size:12px;text-align:center}
.bgrp{display:flex;gap:8px;margin-left:auto;align-items:flex-end;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 14px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;transition:filter .15s,transform .1s;white-space:nowrap}
.btn:hover{filter:brightness(1.08);transform:translateY(-1px)}
.btn-p{background:var(--teal);color:#fff}
.btn-g{background:var(--border);color:var(--text)}
.btn-r{background:var(--red);color:#fff}
.krow{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px;padding:18px 28px 4px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}
.kpi.kw::before{background:linear-gradient(90deg,#f0a500,#ffcc44)}
.kpi.ka::before{background:linear-gradient(90deg,var(--red),#e74c3c)}
.kpi.ko::before{background:linear-gradient(90deg,var(--green),#27ae60)}
.kl{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.kv{font-size:18px;font-weight:700;letter-spacing:-.3px;line-height:1}
.ks{font-size:11px;color:var(--muted);margin-top:5px}
.vp{color:var(--green);font-weight:600}
.vn{color:var(--red);font-weight:600}
.vz{color:var(--muted)}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase}
.br{background:#fde8e6;color:var(--red)}.bg{background:#e6f5ec;color:var(--green)}
.tsec{padding:16px 28px 32px}
.thead-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.ttitle{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.tctrl{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.sw{position:relative}
.sw input{border:2px solid var(--teal);border-radius:6px;padding:8px 12px 8px 34px;font-size:13px;width:280px;background:#fff;box-shadow:0 0 0 3px rgba(0,144,168,.08)}
.sw input:focus{outline:none;border-color:var(--navy);box-shadow:0 0 0 3px rgba(0,144,168,.18)}
.sw::before{content:'🔍';position:absolute;left:9px;top:50%;transform:translateY(-50%);font-size:13px;pointer-events:none}
.tw{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}
table{width:100%;border-collapse:collapse;table-layout:fixed;min-width:1600px}
thead th{background:var(--navy);color:#c8d8ec;padding:10px 12px;font-size:11px;font-weight:600;text-align:right;white-space:nowrap;user-select:none;letter-spacing:.3px;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
thead th.nosort{cursor:default}
thead th.left{text-align:left}
.si{display:inline-block;width:14px;text-align:center;opacity:.55;font-size:10px}
tr.dr{background:var(--surface)}
tr.dr.alt{background:var(--row-alt)}
tr.dr td{padding:7px 12px;border-bottom:1px solid var(--border);font-size:12px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis}
tr.dr td.left{text-align:left}
tr.dr:hover td{background:var(--hover)}
tr.row-doc{background:#1e3267;cursor:pointer}
tr.row-doc:hover{background:#162550}
tr.row-doc td{color:#e8f0fc;font-weight:700;padding:10px 14px;font-size:12px;border-bottom:2px solid #0d1b3e;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.row-doc td.left{text-align:left}
tr.dr-sub{background:var(--surface)}
tr.dr-sub.alt{background:var(--row-alt)}
tr.dr-sub:hover td{background:var(--hover)}
tr.dr-sub td{padding:7px 12px 7px 36px;border-bottom:1px solid var(--border);font-size:11.5px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis}
tr.dr-sub td.left{text-align:left}
td.mono{font-family:'Consolas','Courier New',monospace;font-size:11.5px;color:var(--muted)}
tfoot td{background:#e8f0f8;font-weight:700;border-top:2px solid var(--teal);padding:9px 12px;font-size:12px;text-align:right}
tfoot td.left{text-align:left}
.empty{text-align:center;padding:56px;color:var(--muted)}
#pag{display:flex;justify-content:center;align-items:center;gap:6px;padding:14px 28px;flex-wrap:wrap}
</style>
</head>
<body>
<div id="ldg" style="position:fixed;inset:0;background:rgba(13,27,62,.88);display:flex;flex-direction:column;align-items:center;justify-content:center;z-index:9999;color:#fff">
  <div style="font-size:28px;margin-bottom:14px">⏳</div>
  <div style="font-size:15px;font-weight:600;letter-spacing:.3px">Carregando dados…</div>
  <div style="font-size:12px;color:#9ab0cc;margin-top:8px">Aguarde um instante</div>
</div>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">📋</div>
    <h1>DDR POR LANÇAMENTO<span>SIGGO · Ano Exercício 2026</span></h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <span id="ts">Gerado em: {timestamp}</span>
</header>

<div class="fbar">
  <div class="fg">
    <label>Gestão</label>
    <select id="fg" onchange="aplicar()"><option value="">Todas</option></select>
  </div>
  <div class="fg">
    <label>Unidade Gestora</label>
    <div class="ug-wrap">
      <input id="fu" class="ug-input" type="text" placeholder="Código ou nome da UG…" autocomplete="off"
             oninput="ugInput()" onfocus="ugFocus()" onblur="ugBlur()">
      <button class="ug-clear" id="fu-clr" onclick="limparUG()" title="Limpar">✕</button>
      <div class="ug-dd" id="fu-dd"></div>
    </div>
  </div>
  <div class="fg">
    <label>Mês</label>
    <select id="fm" onchange="aplicar()"><option value="">Todos</option></select>
  </div>
  <div class="fg">
    <label>Conta Contábil (a)</label>
    <select id="fca" onchange="aplicar()"><option value="">Todas</option></select>
  </div>
  <div class="fg">
    <label>Fonte (a)</label>
    <select id="ffa" onchange="aplicar()"><option value="">Todas</option></select>
  </div>
  <div class="fg">
    <label>Conta Contábil (b)</label>
    <select id="fcb" onchange="aplicar()"><option value="">Todas</option></select>
  </div>
  <div class="fg">
    <label>Fonte (b)</label>
    <select id="ffb" onchange="aplicar()"><option value="">Todas</option></select>
  </div>
  <div class="fg">
    <label>Exibir saldos</label>
    <select id="fs" onchange="aplicar()">
      <option value="todos">Todos</option>
      <option value="dif_nz" selected>Com diferença</option>
      <option value="dif_pos">Diferença positiva</option>
      <option value="dif_neg">Diferença negativa</option>
    </select>
  </div>
  <div class="bgrp">
    <div class="ev-drop">
      <button class="btn btn-b" onclick="toggleEv(event)">📋 Eventos potenciais<span class="ev-cnt-badge" id="ev-cnt">0</span></button>
      <div class="ev-popup" id="ev-popup">
        <div class="ev-popup-hdr">Eventos potenciais causadores <span id="ev-popup-sub"></span></div>
        <div class="ev-popup-body">
          <table class="ev-tbl">
            <thead><tr><th>Evento</th><th>Docs</th><th>Dif. Acum.</th></tr></thead>
            <tbody id="ev-tbody"></tbody>
          </table>
        </div>
        <div class="ev-popup-foot">
          <button class="btn btn-b" onclick="exportarEventos()">⬇ Exportar CSV</button>
        </div>
      </div>
    </div>
    <button class="btn btn-r" onclick="somenteDif()">⚠ Somente Diferenças</button>
    <button class="btn btn-g" onclick="limpar()">↺ Limpar filtros</button>
    <button class="btn btn-p" onclick="exportar()">↓ Exportar CSV</button>
  </div>
</div>

<div class="krow" id="krow"></div>

<div class="tsec">
  <div class="thead-row">
    <span class="ttitle" id="cnt"></span>
    <div class="tctrl">
      <div class="ug-wrap" style="min-width:220px">
        <input id="emit-in" class="ug-input" type="text" placeholder="Gestão-UG Emitente…" autocomplete="off"
               oninput="emitInput()" onfocus="emitFocus()" onblur="emitBlur()">
        <button class="ug-clear" id="emit-clr" onclick="limparEmit()" title="Limpar">✕</button>
        <div class="ug-dd" id="emit-dd"></div>
      </div>
      <div class="sw"><input id="busca" type="text" placeholder="Buscar por nº documento ou evento…" oninput="debounce(aplicar)"></div>
    </div>
  </div>
  <div class="tw">
    <table>
      <thead><tr>
        <th class="nosort left" style="width:220px">Gestão · UG</th>
        <th class="left" onclick="sortBy('CONTA_A')" style="width:120px">Conta Contábil (a) <span id="s_CONTA_A" class="si">⇅</span></th>
        <th onclick="sortBy('FONTE_A')" style="width:82px">Fonte (a) <span id="s_FONTE_A" class="si">⇅</span></th>
        <th onclick="sortBy('MOV_A')" style="width:115px">Mov. Conta (a) <span id="s_MOV_A" class="si">⇅</span></th>
        <th class="left" onclick="sortBy('CONTA_B')" style="width:120px">Conta Contábil (b) <span id="s_CONTA_B" class="si">⇅</span></th>
        <th onclick="sortBy('FONTE_B')" style="width:82px">Fonte (b) <span id="s_FONTE_B" class="si">⇅</span></th>
        <th onclick="sortBy('MOV_B')" style="width:115px">Mov. Conta (b) <span id="s_MOV_B" class="si">⇅</span></th>
        <th onclick="sortBy('DIFERENCA')" style="width:115px">Diferença (a−b) <span id="s_DIFERENCA" class="si">⇅</span></th>
        <th onclick="sortBy('DALANCAMENTO')" style="width:88px">Data Lanç. <span id="s_DALANCAMENTO" class="si">↑</span></th>
        <th class="left nosort" style="width:110px">Gestão-UG Emitente</th>
        <th class="left" onclick="sortBy('NUDOCUMENTO')" style="width:130px">Nº Documento <span id="s_NUDOCUMENTO" class="si">⇅</span></th>
        <th class="left" onclick="sortBy('COEVENTO')" style="width:75px">Evento <span id="s_COEVENTO" class="si">⇅</span></th>
      </tr></thead>
      <tbody id="tbody"></tbody>
      <tfoot id="tfoot"></tfoot>
    </table>
  </div>
</div>
<div id="pag"></div>

<script>
const DADOS_GZ="{dados}";
const UGS={ugs};
const MESES=['Saldo Inicial','Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro','Encerramento do Exercício','Encerramento do Exercício'];
const PS=100;
let ALL=[],fil=[],filDocs=[],ugSel='',emitSel='',pg=1,sortCol='DALANCAMENTO',sortDir=1;
let _dbt; function debounce(fn,ms=300){clearTimeout(_dbt);_dbt=setTimeout(fn,ms);}
const brl=v=>(v===null||v===undefined||isNaN(v))?'—':Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
const vc=v=>(v===null||v===undefined||isNaN(v))||Math.abs(v)<0.005?'vz':v>0?'vp':'vn';
const num=v=>(v===null||v===undefined||isNaN(v))?0:Number(v);
const fmt9=v=>{if(v===null||v===undefined)return'—';const s=String(Math.round(Number(v)));return s.padStart(9,'0');};
const fmtGestao=v=>String(v).padStart(5,'0');
const ugNome=c=>UGS[String(c)]||'';

/* ── UG autocomplete ── */
const ugList=Object.entries(UGS).map(([c,n])=>({c,n})).sort((a,b)=>a.c.localeCompare(b.c));
function ugInput(){
  const v=document.getElementById('fu').value.toLowerCase();
  const m=v?ugList.filter(u=>u.c.includes(v)||u.n.toLowerCase().includes(v)):ugList;
  renderDd(m);
  if(!v){ugSel='';document.getElementById('fu-clr').style.display='none';aplicar();}
}
function ugFocus(){const v=document.getElementById('fu').value.toLowerCase();renderDd(v?ugList.filter(u=>u.c.includes(v)||u.n.toLowerCase().includes(v)):ugList);}
function ugBlur(){setTimeout(()=>document.getElementById('fu-dd').style.display='none',200);}
function renderDd(lista){
  const dd=document.getElementById('fu-dd');
  if(!lista.length){dd.innerHTML='<div class="ug-dd-empty">Nenhuma UG encontrada</div>';dd.style.display='block';return;}
  dd.innerHTML=lista.slice(0,60).map(u=>'<div class="ug-dd-item" onmousedown="selUG(\''+u.c+'\',\''+u.n.replace(/'/g,"\\'")+'\')">'+'<strong>'+u.c+'</strong>'+(u.n?' — '+u.n:'')+'</div>').join('');
  dd.style.display='block';
}
function selUG(c,n){ugSel=c;document.getElementById('fu').value=n?c+' — '+n:c;document.getElementById('fu-clr').style.display='block';document.getElementById('fu-dd').style.display='none';aplicar();}
function limparUG(){ugSel='';document.getElementById('fu').value='';document.getElementById('fu-clr').style.display='none';aplicar();}

/* ── Emitente autocomplete ── */
let emitList=[];
function emitInput(){
  const v=document.getElementById('emit-in').value.toLowerCase();
  const m=v?emitList.filter(e=>e.includes(v)):emitList;
  renderEmitDd(m);
  if(!v){emitSel='';document.getElementById('emit-clr').style.display='none';debounce(aplicar);}
}
function emitFocus(){const v=document.getElementById('emit-in').value.toLowerCase();renderEmitDd(v?emitList.filter(e=>e.includes(v)):emitList);}
function emitBlur(){setTimeout(()=>document.getElementById('emit-dd').style.display='none',200);}
function renderEmitDd(lista){
  const dd=document.getElementById('emit-dd');
  if(!lista.length){dd.innerHTML='<div class="ug-dd-empty">Nenhum emitente encontrado</div>';dd.style.display='block';return;}
  dd.innerHTML=lista.slice(0,80).map(e=>'<div class="ug-dd-item" onmousedown="selEmit(\''+e+'\')"><strong>'+e+'</strong></div>').join('');
  dd.style.display='block';
}
function selEmit(v){emitSel=v;document.getElementById('emit-in').value=v;document.getElementById('emit-clr').style.display='block';document.getElementById('emit-dd').style.display='none';aplicar();}
function limparEmit(){emitSel='';document.getElementById('emit-in').value='';document.getElementById('emit-clr').style.display='none';aplicar();}

/* ── Agregação por documento ── */
function buildDocs(rows){
  const map={};
  rows.forEach(r=>{
    const k=String(r.NUDOCUMENTO)+'|'+fmtGestao(r.COGESTAO_EMIT)+'-'+String(r.COUG_EMIT);
    if(!map[k])map[k]={NUDOCUMENTO:r.NUDOCUMENTO,COGESTAO_EMIT:r.COGESTAO_EMIT,COUG_EMIT:r.COUG_EMIT,MOV_A:0,MOV_B:0,DIFERENCA:0,rows:[],minDate:null};
    const d=map[k];
    d.MOV_A+=num(r.MOV_A);d.MOV_B+=num(r.MOV_B);d.DIFERENCA+=num(r.DIFERENCA);
    d.rows.push(r);
    if(r.DALANCAMENTO&&(!d.minDate||r.DALANCAMENTO<d.minDate))d.minDate=r.DALANCAMENTO;
  });
  return Object.values(map);
}

/* ── Filtros ── */
function aplicar(){
  const g=document.getElementById('fg').value;
  const fm=document.getElementById('fm').value;
  const fca=document.getElementById('fca').value;
  const ffa=document.getElementById('ffa').value;
  const fcb=document.getElementById('fcb').value;
  const ffb=document.getElementById('ffb').value;
  const sd=document.getElementById('fs').value;
  fil=ALL.filter(r=>{
    if(g&&String(r.COGESTAO)!==g)return false;
    if(ugSel&&String(r.COUG)!==ugSel)return false;
    if(fm&&String(r.INMES)!==fm)return false;
    if(fca&&String(r.CONTA_A)!==fca)return false;
    if(ffa){const fa=r.FONTE_A!==null&&r.FONTE_A!==undefined?fmt9(r.FONTE_A):'—';if(fa!==ffa)return false;}
    if(fcb&&String(r.CONTA_B)!==fcb)return false;
    if(ffb){const fb=r.FONTE_B!==null&&r.FONTE_B!==undefined?fmt9(r.FONTE_B):'—';if(fb!==ffb)return false;}
    if(emitSel&&(fmtGestao(r.COGESTAO_EMIT)+'-'+r.COUG_EMIT)!==emitSel)return false;
    const b=document.getElementById('busca').value.trim().toLowerCase();
    if(b&&!String(r.NUDOCUMENTO||'').toLowerCase().includes(b)&&!String(r.COEVENTO!==null&&r.COEVENTO!==undefined?r.COEVENTO:'').toLowerCase().includes(b))return false;
    return true;
  });
  const allDocs=buildDocs(fil);
  filDocs=allDocs.filter(d=>{
    if(sd==='dif_nz')return Math.abs(d.DIFERENCA)>=0.005;
    if(sd==='dif_pos')return d.DIFERENCA>0.005;
    if(sd==='dif_neg')return d.DIFERENCA<-0.005;
    return true;
  });
  aplicarSort();
  pg=1;render();kpis();
  if(document.getElementById('ev-popup').classList.contains('open'))renderEventos();else evDirty=true;
}
function somenteDif(){
  document.getElementById('fg').value='';document.getElementById('fm').value='';
  document.getElementById('fca').value='';document.getElementById('ffa').value='';
  document.getElementById('fcb').value='';document.getElementById('ffb').value='';
  document.getElementById('fs').value='dif_nz';document.getElementById('busca').value='';limparEmit();limparUG();
}
function limpar(){
  document.getElementById('fg').value='';document.getElementById('fm').value='';
  document.getElementById('fca').value='';document.getElementById('ffa').value='';
  document.getElementById('fcb').value='';document.getElementById('ffb').value='';
  document.getElementById('fs').value='dif_nz';document.getElementById('busca').value='';limparEmit();limparUG();
}
function aplicarSort(){
  filDocs.sort((a,b)=>{
    if(sortCol==='MOV_A')return sortDir*(num(a.MOV_A)-num(b.MOV_A));
    if(sortCol==='MOV_B')return sortDir*(num(a.MOV_B)-num(b.MOV_B));
    if(sortCol==='DIFERENCA')return sortDir*(num(a.DIFERENCA)-num(b.DIFERENCA));
    if(sortCol==='NUDOCUMENTO')return sortDir*String(a.NUDOCUMENTO).localeCompare(String(b.NUDOCUMENTO),'pt-BR');
    const pa=a.minDate?a.minDate.split('/').reverse().join(''):'';
    const pb=b.minDate?b.minDate.split('/').reverse().join(''):'';
    return sortDir*(pa<pb?-1:pa>pb?1:0);
  });
}
function sortBy(col){
  if(sortCol===col){sortDir*=-1;}else{sortCol=col;sortDir=(col==='DIFERENCA')?-1:1;}
  document.querySelectorAll('.si').forEach(el=>el.textContent='⇅');
  const sp=document.getElementById('s_'+col);if(sp)sp.textContent=sortDir>0?'↑':'↓';
  aplicarSort();pg=1;render();
}

/* ── Render (dois níveis: documento / lançamento) ── */
function render(){
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  const n=filDocs.length;
  document.getElementById('cnt').textContent=n.toLocaleString('pt-BR')+' documento'+(n!==1?'s':'')+' c/ diferença';
  if(!n){tb.innerHTML='<tr><td colspan="12" class="empty">Nenhum documento com diferença encontrado.</td></tr>';tf.innerHTML='';document.getElementById('pag').innerHTML='';return;}
  const s=(pg-1)*PS,pageDocs=filDocs.slice(s,s+PS);
  let html='';
  pageDocs.forEach((d,i)=>{
    const did='doc_'+(s+i);
    const emitLabel=fmtGestao(d.COGESTAO_EMIT)+'-'+String(d.COUG_EMIT);
    const ugsSet=[...new Set(d.rows.map(r=>String(r.COUG)))];
    const ugInfo=ugsSet.length===1?'UG '+ugsSet[0]+(ugNome(ugsSet[0])?' · '+ugNome(ugsSet[0]):''):ugsSet.length+' UGs contábeis';
    html+='<tr class="row-doc" onclick="toggleDoc(\''+did+'\')">'
      +'<td class="left"><span class="tog" id="tog_'+did+'">▶</span> '+String(d.NUDOCUMENTO)
      +' · '+emitLabel
      +' <span style="font-size:10px;font-weight:400;opacity:.6">('+d.rows.length+' lanç. · '+ugInfo+')</span></td>'
      +'<td></td><td></td>'
      +'<td class="'+vc(d.MOV_A)+'">'+brl(d.MOV_A)+'</td>'
      +'<td></td><td></td>'
      +'<td class="'+vc(d.MOV_B)+'">'+brl(d.MOV_B)+'</td>'
      +'<td class="'+vc(d.DIFERENCA)+'">'+brl(d.DIFERENCA)+'</td>'
      +'<td style="text-align:right;font-family:\'Consolas\',monospace;font-size:11.5px;opacity:.75">'+(d.minDate||'')+'</td>'
      +'<td class="left" style="opacity:.8">'+emitLabel+'</td>'
      +'<td class="left" style="font-family:\'Consolas\',\'Courier New\',monospace;font-size:11.5px">'+String(d.NUDOCUMENTO)+'</td>'
      +'<td></td></tr>';
    d.rows.forEach((r,j)=>{
      const k=String(r.COUG),nome=ugNome(k);
      const ugLabel='GESTÃO '+fmtGestao(r.COGESTAO)+' · UG '+k+(nome?' – '+nome:'');
      const contaA=r.CONTA_A!==null&&r.CONTA_A!==undefined?r.CONTA_A:'—';
      const contaB=r.CONTA_B!==null&&r.CONTA_B!==undefined?r.CONTA_B:'—';
      html+='<tr class="dr-sub'+(j%2?' alt':'')+'" data-doc="'+did+'" style="display:none">'
        +'<td class="left" title="'+ugLabel+'">'+ugLabel+'</td>'
        +'<td class="mono left">'+contaA+'</td>'
        +'<td class="mono" style="text-align:right">'+fmt9(r.FONTE_A)+'</td>'
        +'<td class="'+vc(r.MOV_A)+'">'+brl(r.MOV_A)+'</td>'
        +'<td class="mono left">'+contaB+'</td>'
        +'<td class="mono" style="text-align:right">'+fmt9(r.FONTE_B)+'</td>'
        +'<td class="'+vc(r.MOV_B)+'">'+brl(r.MOV_B)+'</td>'
        +'<td class="'+vc(r.DIFERENCA)+'">'+brl(r.DIFERENCA)+'</td>'
        +'<td style="text-align:right;font-family:\'Consolas\',monospace;font-size:11.5px;color:var(--muted)">'+(r.DALANCAMENTO||'')+'</td>'
        +'<td class="mono left">'+emitLabel+'</td>'
        +'<td class="mono left">'+String(r.NUDOCUMENTO)+'</td>'
        +'<td class="mono left">'+(r.COEVENTO!==null&&r.COEVENTO!==undefined?r.COEVENTO:'—')+'</td>'
        +'</tr>';
    });
  });
  tb.innerHTML=html;
  const totA=filDocs.reduce((a,d)=>a+d.MOV_A,0);
  const totB=filDocs.reduce((a,d)=>a+d.MOV_B,0);
  const totD=filDocs.reduce((a,d)=>a+d.DIFERENCA,0);
  const totUGs=new Set(filDocs.flatMap(d=>d.rows.map(r=>r.COUG))).size;
  tf.innerHTML='<td class="left">Totais · '+n.toLocaleString('pt-BR')+' documentos · '+totUGs+' UGs</td>'
    +'<td></td><td></td>'
    +'<td class="'+vc(totA)+'">'+brl(totA)+'</td>'
    +'<td></td><td></td>'
    +'<td class="'+vc(totB)+'">'+brl(totB)+'</td>'
    +'<td class="'+vc(totD)+'">'+brl(totD)+'</td>'
    +'<td></td><td></td><td></td><td></td>';
  paginar();
}
function toggleDoc(id){
  const tog=document.getElementById('tog_'+id);
  const open=tog&&tog.textContent.trim()==='▼';
  document.querySelectorAll('[data-doc="'+id+'"]').forEach(tr=>tr.style.display=open?'none':'');
  if(tog)tog.textContent=open?'▶':'▼';
}
function paginar(){
  const pag=document.getElementById('pag'),pages=Math.ceil(filDocs.length/PS);
  if(pages<=1){pag.innerHTML='';return;}
  const s=(pg-1)*PS+1,e=Math.min(pg*PS,filDocs.length);
  let b='<button onclick="ir(pg-1)" '+(pg===1?'disabled':'')+' style="padding:4px 10px;border:1px solid var(--border);border-radius:5px;cursor:pointer;background:#fff">‹</button>';
  for(let i=1;i<=pages;i++){
    if(i===1||i===pages||Math.abs(i-pg)<=1)
      b+='<button onclick="ir('+i+')" style="padding:4px 10px;border:1px solid var(--border);border-radius:5px;cursor:pointer;background:'+(i===pg?'var(--teal)':'#fff')+';color:'+(i===pg?'#fff':'inherit')+'">'+i+'</button>';
    else if(Math.abs(i-pg)===2)
      b+='<button disabled style="border:none;background:none;padding:4px 6px">…</button>';
  }
  b+='<button onclick="ir(pg+1)" '+(pg===pages?'disabled':'')+' style="padding:4px 10px;border:1px solid var(--border);border-radius:5px;cursor:pointer;background:#fff">›</button>';
  pag.innerHTML='<span style="font-size:12px;color:var(--muted)">Mostrando '+s.toLocaleString('pt-BR')+'–'+e.toLocaleString('pt-BR')+' de '+filDocs.length.toLocaleString('pt-BR')+'</span><div style="display:flex;gap:4px;flex-wrap:wrap">'+b+'</div>';
}
function ir(p){const pages=Math.ceil(filDocs.length/PS);if(p<1||p>pages)return;pg=p;render();window.scrollTo({top:0,behavior:'smooth'});}

/* ── KPIs ── */
function kpis(){
  const n=filDocs.length;
  const totA=filDocs.reduce((a,d)=>a+d.MOV_A,0);
  const totB=filDocs.reduce((a,d)=>a+d.MOV_B,0);
  const dif=filDocs.reduce((a,d)=>a+d.DIFERENCA,0);
  const dc=Math.abs(dif)<0.01?'ko':dif>0?'kw':'ka';
  document.getElementById('krow').innerHTML=
    '<div class="kpi"><div class="kl">Mov. Contas 62292010X, 63XXXXXXX, 721190300 e 827110401 (a)</div><div class="kv '+vc(totA)+'">'+brl(totA)+'</div></div>'
   +'<div class="kpi"><div class="kl">Mov. Conta 8211XXXXX (b)</div><div class="kv '+vc(totB)+'">'+brl(totB)+'</div></div>'
   +'<div class="kpi '+dc+'"><div class="kl">Diferença (a−b)</div><div class="kv '+vc(dif)+'">'+brl(dif)+'</div>'
   +'<div class="ks"><span class="badge '+(n>0?'br':'bg')+'">'+n.toLocaleString('pt-BR')+' documento'+(n!==1?'s':'')+' c/ dif.</span></div></div>';
}

/* ── Eventos Potenciais ── */
let evData=[],evDirty=true;
function renderEventos(){
  const evMap={};
  filDocs.flatMap(d=>d.rows).filter(r=>Math.abs(num(r.DIFERENCA))>=0.005).forEach(r=>{
    const ev=String(r.COEVENTO!==null&&r.COEVENTO!==undefined?r.COEVENTO:'').trim();
    if(!ev||ev==='—'||ev==='null')return;
    if(!evMap[ev])evMap[ev]={docs:new Set(),dif:0};
    evMap[ev].docs.add(String(r.NUDOCUMENTO)+'|'+fmtGestao(r.COGESTAO_EMIT)+'-'+r.COUG_EMIT);
    evMap[ev].dif+=num(r.DIFERENCA);
  });
  evData=Object.entries(evMap).filter(e=>Math.abs(e[1].dif)>=0.005).sort((a,b)=>b[1].docs.size-a[1].docs.size);
  const n=evData.length;
  document.getElementById('ev-cnt').textContent=n;
  document.getElementById('ev-popup-sub').textContent=n+' evento'+(n!==1?'s':'');
  const etb=document.getElementById('ev-tbody');
  if(!n){etb.innerHTML='<tr><td colspan="3" style="text-align:center;color:var(--muted);padding:16px">Nenhum evento nos dados filtrados.</td></tr>';return;}
  etb.innerHTML=evData.map(e=>'<tr><td>'+e[0]+'</td><td>'+e[1].docs.size.toLocaleString('pt-BR')+'</td><td class="'+vc(e[1].dif)+'">'+brl(e[1].dif)+'</td></tr>').join('');
}
function toggleEv(e){
  e.stopPropagation();
  const p=document.getElementById('ev-popup');
  p.classList.toggle('open');
  if(p.classList.contains('open')&&evDirty){renderEventos();evDirty=false;}
}
document.addEventListener('click',function(){const p=document.getElementById('ev-popup');if(p)p.classList.remove('open');});
function exportarEventos(){
  if(!evData.length)return alert('Nenhum evento para exportar.');
  const linhas=['Evento;Docs;Diferenca Total'].concat(evData.map(e=>e[0]+';'+e[1].docs.size+';'+String(e[1].dif).replace('.',',')));
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),download:'eventos_potenciais_ddr_lancamento.csv'});
  a.click();URL.revokeObjectURL(a.href);
}

/* ── Exportar CSV ── */
function exportar(){
  if(!filDocs.length)return alert('Nenhum dado para exportar.');
  const lancamentos=filDocs.flatMap(d=>d.rows);
  const cols=['COGESTAO','COUG','CONTA_A','FONTE_A','MOV_A','CONTA_B','FONTE_B','MOV_B','DIFERENCA','DALANCAMENTO','COGESTAO_EMIT','COUG_EMIT','NUDOCUMENTO','COEVENTO'];
  const hdrs=['Gestao Contab','UG Contab','Conta Contabil (a)','Fonte (a)','Mov Conta (a)','Conta Contabil (b)','Fonte (b)','Mov Conta (b)','Diferenca (a-b)','Data Lancamento','Gestao Emitente','UG Emitente','Numero Documento','Evento'];
  const cel=v=>{if(typeof v==='number')return String(v).replace('.',',');const s=v??'';return /^\d+$/.test(s)?`="${s}"`:s;};
  const linhas=[hdrs.join(';')].concat(lancamentos.map(r=>cols.map(c=>cel(r[c])).join(';')));
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),download:'ddr_lancamento.csv'});
  a.click();URL.revokeObjectURL(a.href);
}

/* ── Init ── */
function init(){
  const gestoes=[...new Set(ALL.map(r=>String(r.COGESTAO)))].sort();
  const sg=document.getElementById('fg');
  gestoes.forEach(g=>{const o=document.createElement('option');o.value=g;o.textContent=fmtGestao(g);sg.appendChild(o);});

  const meses=[...new Set(ALL.map(r=>r.INMES))].filter(m=>m!==null).sort((a,b)=>a-b);
  const smEl=document.getElementById('fm');
  meses.forEach(m=>{const o=document.createElement('option');o.value=m;o.textContent=m+(MESES[m]?' — '+MESES[m]:'');smEl.appendChild(o);});

  const contasA=[...new Set(ALL.map(r=>r.CONTA_A).filter(v=>v!==null&&v!==undefined))].sort((a,b)=>Number(a)-Number(b));
  document.getElementById('fca').append(...contasA.map(c=>{const o=document.createElement('option');o.value=o.textContent=c;return o;}));

  const fontesA=[...new Set(ALL.map(r=>r.FONTE_A!==null&&r.FONTE_A!==undefined?fmt9(r.FONTE_A):'—'))].sort();
  document.getElementById('ffa').append(...fontesA.map(f=>{const o=document.createElement('option');o.value=o.textContent=f;return o;}));

  const contasB=[...new Set(ALL.map(r=>r.CONTA_B).filter(v=>v!==null&&v!==undefined))].sort((a,b)=>Number(a)-Number(b));
  document.getElementById('fcb').append(...contasB.map(c=>{const o=document.createElement('option');o.value=o.textContent=c;return o;}));

  const fontesB=[...new Set(ALL.map(r=>r.FONTE_B!==null&&r.FONTE_B!==undefined?fmt9(r.FONTE_B):'—'))].sort();
  document.getElementById('ffb').append(...fontesB.map(f=>{const o=document.createElement('option');o.value=o.textContent=f;return o;}));

  emitList=[...new Set(ALL.map(r=>fmtGestao(r.COGESTAO_EMIT)+'-'+r.COUG_EMIT))].sort();

  aplicar();
}

/* ── Descompressão gzip e arranque ── */
(async()=>{
  try{
    const bin=atob(DADOS_GZ);
    const bytes=new Uint8Array(bin.length);
    for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
    const ds=new DecompressionStream('gzip');
    const w=ds.writable.getWriter();w.write(bytes);w.close();
    const chunks=[];const rd=ds.readable.getReader();
    while(true){const{value,done}=await rd.read();if(done)break;chunks.push(value);}
    const tot=chunks.reduce((a,c)=>a+c.length,0);
    const buf=new Uint8Array(tot);let off=0;
    for(const c of chunks){buf.set(c,off);off+=c.length;}
    ALL=JSON.parse(new TextDecoder().decode(buf));
  }catch(e){console.error('Erro ao descomprimir dados:',e);}
  document.getElementById('ldg').style.display='none';
  init();
})();
</script>
</body>
</html>"""


# ── Oracle ─────────────────────────────────────────────────────────────────────
oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")


def extrair(ug: str | None) -> pd.DataFrame:
    if ug:
        filtro_ug = "AND l.COUGCONTAB = :ug"
        params = {"ug": int(ug)}
    else:
        filtro_ug = ""
        params = {}

    sql = SQL.format(schema=SCHEMA, filtro_ug=filtro_ug)

    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle…")
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        print(f"[{datetime.now():%H:%M:%S}] Executando consulta…")
        df = pd.read_sql(sql, conn, params=params if params else None)

    for col in ["MOV_A", "MOV_B", "DIFERENCA"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    print(f"[{datetime.now():%H:%M:%S}] {len(df):,} lançamentos retornados.")
    return df


def extrair_ugs() -> dict:
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        df = pd.read_sql(f"SELECT COUG, NOUG FROM {SCHEMA}VUNIDADEGESTORA", conn)
    return {str(int(r.COUG)): r.NOUG for r in df.itertuples() if r.NOUG}


# ── Geração do HTML ────────────────────────────────────────────────────────────
def gerar_html(df: pd.DataFrame, ugs: dict) -> str:
    registros = df.to_dict(orient="records")
    for r in registros:
        for k, v in r.items():
            if isinstance(v, float) and pd.isna(v):
                r[k] = None
            elif hasattr(v, "item"):
                r[k] = v.item()
            elif hasattr(v, "__float__") and not isinstance(v, (str, type(None))):
                fv = float(v)
                r[k] = int(fv) if fv == int(fv) else fv

    json_str = json.dumps(registros, ensure_ascii=False, separators=(",", ":"))
    dados_b64 = base64.b64encode(
        gzip.compress(json_str.encode("utf-8"), compresslevel=9)
    ).decode()

    print(f"[{datetime.now():%H:%M:%S}] JSON original: {len(json_str)/1024/1024:.1f} MB  "
          f"-> comprimido+b64: {len(dados_b64)/1024/1024:.1f} MB")

    html = HTML_TEMPLATE
    html = html.replace("{dados}", dados_b64)
    html = html.replace("{ugs}", json.dumps(ugs, ensure_ascii=False))
    html = html.replace("{timestamp}", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    return html


# ── Publicação GitHub (via git push) ───────────────────────────────────────────
def publicar_github(html_path: Path) -> str:
    pasta = str(html_path.parent)
    msg = f"chore: atualiza DDR por lancamento — {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    subprocess.run(["git", "-C", pasta, "add", html_path.name], check=True)
    subprocess.run(["git", "-C", pasta, "commit", "-m", msg], check=True)
    subprocess.run(["git", "-C", pasta, "pull", "--rebase", "--autostash", "origin", GITHUB_BRANCH], check=True)
    subprocess.run(["git", "-C", pasta, "push", "origin", GITHUB_BRANCH], check=True)
    return f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}/{ARQUIVO_HTML}"


# ── Main ────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="DDR por Lançamento — extrai do Oracle e publica no GitHub Pages.")
    parser.add_argument("--ug",      type=str,            default=None)
    parser.add_argument("--no-push", action="store_true", default=False)
    args = parser.parse_args()

    try:
        df = extrair(args.ug)
        print(f"[{datetime.now():%H:%M:%S}] Buscando nomes das Unidades Gestoras…")
        ugs = extrair_ugs()
        print(f"[{datetime.now():%H:%M:%S}] {len(ugs):,} UGs carregadas.")
    except oracledb.DatabaseError as e:
        print(f"\nErro de banco de dados: {e}", file=sys.stderr)
        sys.exit(1)

    html = gerar_html(df, ugs)
    out = Path(__file__).parent / ARQUIVO_HTML
    out.write_text(html, encoding="utf-8")
    print(f"[{datetime.now():%H:%M:%S}] HTML salvo: {out} ({out.stat().st_size/1024/1024:.1f} MB)")

    if not args.no_push:
        print(f"[{datetime.now():%H:%M:%S}] Publicando no GitHub…")
        url = publicar_github(out)
        print(f"[{datetime.now():%H:%M:%S}] Publicado com sucesso.\n  -> {url}")

    dif_nz = int((df["DIFERENCA"].abs() >= 0.005).sum())
    print("\n-- Resumo -----------------------------------------------------------")
    print(f"  Linhas retornadas   : {len(df):,}")
    print(f"  UGs                 : {df['COUG'].nunique():,}")
    print(f"  Com diferença != 0  : {dif_nz:,}")
    print(f"  Diferença total     : R$ {float(df['DIFERENCA'].sum()):,.2f}")
    print("---------------------------------------------------------------------\n")


if __name__ == "__main__":
    main()
