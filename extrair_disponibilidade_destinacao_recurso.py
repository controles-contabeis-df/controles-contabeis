"""
Disponibilidade por Destinacao de Recurso por Saldo — SIGGO/Oracle
Compara saldos das contas orcamentarias (6XXXXXXXX/7XXXXXXXX) com as
contas de controle (8XXXXXXXX) correspondentes, conforme MCASP 11a edicao.
Gera HTML autocontido e publica no GitHub Pages.

Uso:
    python extrair_disponibilidade_destinacao_recurso.py
    python extrair_disponibilidade_destinacao_recurso.py --ug 10101
    python extrair_disponibilidade_destinacao_recurso.py --no-push
"""

import argparse
import base64
import gzip
import json
from datetime import datetime
from pathlib import Path

import oracledb
import pandas as pd

# ── Conexão Oracle ─────────────────────────────────────────────────────────────
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
ARQUIVO_HTML  = "disponibilidade_destinacao_recurso.html"

# ── SQL ────────────────────────────────────────────────────────────────────────
# Correlacao conta orcamentaria/equivalente x conta(s) de controle:
#   622920101 + 631100000 + 631810000 = 821120100   (fonte via NE/NERP)
#   622920102 + 631200000 + 631820000 = 821120200   (fonte via NE/NERP)
#   622920103 + 631300000 + 6321101XX = 821130200 + 821130100  (fonte via NE/NERP)
#   622920104 + 631400000 + 63221XXXX = 821140000   (fonte via NE/NERP)
#   827110401 = 821130300   (fonte direto no COCONTACORRENTE)
#   721190300 = 821110100 + 821110200 + 821120100  (fonte direto no COCONTACORRENTE)
# Contas 63XXXXXXX = Restos a Pagar; fonte via NERESTOPAGAR (NEs de anos anteriores)
SQL = """
WITH orc1 AS (
    SELECT
        sc.COGESTAO,
        sc.COUG,
        sc.COCONTACONTABIL,
        ne.COFONTE,
        SUM(sc.VACREDITO - sc.VADEBITO) AS VLSALDO_ORC
    FROM
        {schema}VSALDOCONTABIL sc
        INNER JOIN {schema}NOTAEMPENHO ne
            ON ne.NUNE = SUBSTR(sc.COCONTACORRENTE, 1, 11)
           AND ne.COGESTAO = sc.COGESTAO
           AND ne.COUG = sc.COUG
    WHERE
        sc.COCONTACONTABIL IN (622920101, 622920102, 622920103, 622920104)
        {filtro_ug_1}
        {filtro_mes}
    GROUP BY
        sc.COGESTAO,
        sc.COUG,
        sc.COCONTACONTABIL,
        ne.COFONTE
),
orc_rp AS (
    -- Restos a Pagar (63XXXXXXX): fonte via NERESTOPAGAR; mapeados ao codigo 622920XXX equivalente
    SELECT
        sc.COGESTAO,
        sc.COUG,
        CASE
            WHEN sc.COCONTACONTABIL IN (631100000, 631810000)                        THEN 622920101
            WHEN sc.COCONTACONTABIL IN (631200000, 631820000)                        THEN 622920102
            WHEN sc.COCONTACONTABIL IN (631300000, 632110100, 632110200,
                                        632110300, 632110400)                        THEN 622920103
            WHEN sc.COCONTACONTABIL IN (631400000)
              OR (sc.COCONTACONTABIL BETWEEN 632210000 AND 632219999)               THEN 622920104
        END AS COCONTACONTABIL,
        ne.COFONTE,
        SUM(sc.VACREDITO - sc.VADEBITO) AS VLSALDO_ORC
    FROM
        {schema}VSALDOCONTABIL sc
        INNER JOIN {schema}NERESTOPAGAR ne
            ON ne.NUNE = SUBSTR(sc.COCONTACORRENTE, 1, 11)
           AND ne.COGESTAO = sc.COGESTAO
           AND ne.COUG = sc.COUG
    WHERE (
        sc.COCONTACONTABIL IN (
            631100000, 631810000,
            631200000, 631820000,
            631300000, 632110100, 632110200, 632110300, 632110400,
            631400000
        )
        OR (sc.COCONTACONTABIL BETWEEN 632210000 AND 632219999)
    )
        {filtro_ug_1}
        {filtro_mes}
    GROUP BY
        sc.COGESTAO,
        sc.COUG,
        CASE
            WHEN sc.COCONTACONTABIL IN (631100000, 631810000)                        THEN 622920101
            WHEN sc.COCONTACONTABIL IN (631200000, 631820000)                        THEN 622920102
            WHEN sc.COCONTACONTABIL IN (631300000, 632110100, 632110200,
                                        632110300, 632110400)                        THEN 622920103
            WHEN sc.COCONTACONTABIL IN (631400000)
              OR (sc.COCONTACONTABIL BETWEEN 632210000 AND 632219999)               THEN 622920104
        END,
        ne.COFONTE
),
orc2 AS (
    -- 827110401 (par, 1o digito 8) = credora -> VACREDITO - VADEBITO
    -- 721190300 (impar, 1o digito 7) = devedora -> VADEBITO - VACREDITO
    SELECT
        sc.COGESTAO,
        sc.COUG,
        sc.COCONTACONTABIL,
        TO_NUMBER(SUBSTR(sc.COCONTACORRENTE, 1, 9)) AS COFONTE,
        SUM(
            CASE WHEN sc.COCONTACONTABIL = 721190300
                 THEN sc.VADEBITO - sc.VACREDITO
                 ELSE sc.VACREDITO - sc.VADEBITO
            END
        ) AS VLSALDO_ORC
    FROM
        {schema}VSALDOCONTABIL sc
    WHERE
        sc.COCONTACONTABIL IN (827110401, 721190300)
        {filtro_ug_2}
        {filtro_mes}
    GROUP BY
        sc.COGESTAO,
        sc.COUG,
        sc.COCONTACONTABIL,
        TO_NUMBER(SUBSTR(sc.COCONTACORRENTE, 1, 9))
),
orc_raw AS (
    SELECT * FROM orc1
    UNION ALL
    SELECT * FROM orc_rp
    UNION ALL
    SELECT * FROM orc2
),
orc_all AS (
    SELECT COGESTAO, COUG, COCONTACONTABIL, COFONTE,
           SUM(VLSALDO_ORC) AS VLSALDO_ORC
    FROM orc_raw
    GROUP BY COGESTAO, COUG, COCONTACONTABIL, COFONTE
),
ctl_raw AS (
    SELECT
        sc.COGESTAO,
        sc.COUG,
        sc.COCONTACONTABIL,
        TO_NUMBER(SUBSTR(sc.COCONTACORRENTE, 1, 9)) AS COFONTE,
        SUM(sc.VACREDITO - sc.VADEBITO) AS VLSALDO
    FROM
        {schema}VSALDOCONTABIL sc
    WHERE
        sc.COCONTACONTABIL IN (
            821120100, 821120200, 821130200, 821130100, 821140000,
            821130300, 821110100, 821110200
        )
        {filtro_ug_3}
        {filtro_mes}
    GROUP BY
        sc.COGESTAO,
        sc.COUG,
        sc.COCONTACONTABIL,
        TO_NUMBER(SUBSTR(sc.COCONTACORRENTE, 1, 9))
),
ctl AS (
    SELECT
        COGESTAO,
        COUG,
        COFONTE,
        CASE COCONTACONTABIL
            WHEN 821120100 THEN 622920101
            WHEN 821120200 THEN 622920102
            WHEN 821130200 THEN 622920103
            WHEN 821130100 THEN 622920103
            WHEN 821140000 THEN 622920104
            WHEN 821130300 THEN 827110401
            WHEN 821110100 THEN 721190300
            WHEN 821110200 THEN 721190300
        END AS COCONTACONTABIL,
        VLSALDO
    FROM ctl_raw
    UNION ALL
    -- 821120100 tambem integra o par de 721190300 (MCASP 11a ed.)
    SELECT COGESTAO, COUG, COFONTE, 721190300 AS COCONTACONTABIL, VLSALDO
    FROM ctl_raw
    WHERE COCONTACONTABIL = 821120100
),
ctl_agg AS (
    SELECT
        COGESTAO,
        COUG,
        COCONTACONTABIL,
        COFONTE,
        SUM(VLSALDO) AS VLSALDO_CTL
    FROM ctl
    GROUP BY
        COGESTAO,
        COUG,
        COCONTACONTABIL,
        COFONTE
),
mapa AS (
    SELECT 622920101 AS CONTA_ORC, '821120100' AS CONTA_CTL FROM DUAL UNION ALL
    SELECT 622920102, '821120200' FROM DUAL UNION ALL
    SELECT 622920103, '821130200 + 821130100' FROM DUAL UNION ALL
    SELECT 622920104, '821140000' FROM DUAL UNION ALL
    SELECT 827110401, '821130300' FROM DUAL UNION ALL
    SELECT 721190300, '821110100 + 821110200 + 821120100' FROM DUAL
)
-- Nota: 622920101 agrega tambem 631100000+631810000 (RP); 622920102 agrega 631200000+631820000 (RP);
--       622920103 agrega 631300000+6321101XX (RP); 622920104 agrega 631400000+63221XXXX (RP)
SELECT
    COALESCE(o.COGESTAO, c.COGESTAO)               AS COGESTAO,
    COALESCE(o.COUG, c.COUG)                       AS COUG,
    COALESCE(o.COCONTACONTABIL, c.COCONTACONTABIL) AS CONTA_ORCAMENTARIA,
    COALESCE(o.COFONTE, c.COFONTE)                 AS COFONTE,
    o.VLSALDO_ORC                                  AS SALDO_ORCAMENTARIA,
    m.CONTA_CTL                                    AS CONTA_CONTROLE,
    c.VLSALDO_CTL                                  AS SALDO_CONTROLE,
    NVL(o.VLSALDO_ORC, 0) - NVL(c.VLSALDO_CTL, 0)  AS DIFERENCA
FROM
    orc_all o
    FULL OUTER JOIN ctl_agg c
        ON o.COGESTAO = c.COGESTAO
       AND o.COUG = c.COUG
       AND o.COCONTACONTABIL = c.COCONTACONTABIL
       AND o.COFONTE = c.COFONTE
    LEFT JOIN mapa m
        ON m.CONTA_ORC = COALESCE(o.COCONTACONTABIL, c.COCONTACONTABIL)
ORDER BY
    1, 2, 3, 4
"""

# ── HTML Template ──────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DDR por Saldo — SIGGO</title>
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
}
body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}
header{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}
.hlogo{width:32px;height:32px;background:#0090a8;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}
header h1{font-size:14px;font-weight:700;letter-spacing:.3px}
header h1 span{font-weight:400;color:#9ab0cc;font-size:12px;display:block;text-transform:none;letter-spacing:0;margin-top:1px}
#ts{font-size:11px;color:#7a99bb;white-space:nowrap}
.voltar{font-size:11px;color:#7a99bb;text-decoration:none;display:flex;align-items:center;gap:4px;margin-left:20px;opacity:.8}
.voltar:hover{opacity:1}
.fbar{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
.fg{display:flex;flex-direction:column;gap:4px}
.fg label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.fg select{border:1.5px solid var(--border);border-radius:6px;padding:7px 28px 7px 10px;font-size:12.5px;min-width:150px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E") no-repeat right 9px center;color:var(--text);cursor:pointer;appearance:none}
.fg select:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ug-wrap{position:relative;min-width:200px}
.ug-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text);transition:border-color .15s}
.ug-input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ug-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}
.ug-dd{position:absolute;top:calc(100% + 4px);left:0;right:0;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:240px;overflow-y:auto;display:none}
.ug-dd-item{padding:8px 12px;cursor:pointer;font-size:12.5px;border-bottom:1px solid var(--border)}
.ug-dd-item:last-child{border-bottom:none}
.ug-dd-item:hover{background:var(--hover)}
.ug-dd-item strong{color:var(--navy);font-weight:700}
.ug-dd-empty{padding:12px;color:var(--muted);font-size:12px;text-align:center}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto}
.btn{border:none;border-radius:6px;padding:8px 16px;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:5px;transition:opacity .15s;white-space:nowrap}
.btn:hover{opacity:.85}
.btn-p{background:var(--teal);color:#fff}
.btn-g{background:var(--border);color:var(--text)}
.kpi-section{background:var(--surface);border-bottom:1px solid var(--border)}
.kpi-toggle{display:flex;align-items:center;gap:8px;padding:8px 28px;cursor:pointer;user-select:none;font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.kpi-toggle:hover{background:var(--row-alt)}
.kpi-toggle-arrow{font-size:10px;transition:transform .2s}
.kpi-toggle-arrow.open{transform:rotate(90deg)}
.kpi-body{display:none;flex-direction:column;gap:8px;padding:10px 28px 14px}
.kpi-body.open{display:flex}
.kpi-row{display:flex;flex-wrap:wrap;gap:8px}
.kpi-group{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;flex:1;min-width:260px}
.kpi-group-title{background:var(--navy);color:#c8d8ec;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;padding:5px 12px;cursor:default}
.kpi-group-inner{display:flex;flex-wrap:wrap}
.kpi{padding:9px 14px;position:relative;overflow:hidden;flex:1;min-width:120px;border-right:1px solid var(--border);background:var(--surface)}
.kpi:last-child{border-right:none}
.kpi-total-row{display:flex;flex-wrap:wrap;gap:8px;padding-top:4px;border-top:1px dashed var(--border)}
.kpi-total-row .kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);flex:1;min-width:160px}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}
.kpi.kw::before{background:linear-gradient(90deg,#f0a500,#ffcc44)}
.kpi.ka::before{background:linear-gradient(90deg,var(--red),#e74c3c)}
.kpi.ko::before{background:linear-gradient(90deg,var(--green),#27ae60)}
.kl{font-size:9px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}
.kv{font-size:13px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi-total-row .kv{font-size:20px}
.acct{display:block;font-size:9px;font-weight:400;color:var(--muted);letter-spacing:.2px;margin-top:2px;white-space:normal;text-transform:none}
tr.row-conta .acct{color:#fff;opacity:.7}
.ks{font-size:10.5px;color:var(--muted);margin-top:4px}
.vp{color:var(--green)}
.vn{color:var(--red)}
.vz{color:var(--muted)}
.badge{display:inline-block;border-radius:10px;padding:1px 8px;font-size:10px;font-weight:700}
.br{background:#fde8e6;color:var(--red)}
.bg{background:#e8f5ee;color:var(--green)}
.cnt-bar{padding:8px 28px;font-size:12px;color:var(--muted);background:var(--surface);border-bottom:1px solid var(--border)}
.tw{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto;margin:18px 28px}
table{width:100%;border-collapse:collapse;table-layout:fixed;min-width:1100px}
thead th{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:right;white-space:nowrap;user-select:none;letter-spacing:.3px;overflow:hidden;text-overflow:ellipsis}
thead th:first-child{text-align:left}
/* ── Hierarquia ── */
tr.row-gestao{background:#1e3267;cursor:pointer}
tr.row-gestao:hover{background:#162550}
tr.row-gestao td{color:#e8f0fc;font-weight:700;padding:10px 14px;font-size:12px;border-bottom:2px solid #0d1b3e;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.row-gestao td:first-child{text-align:left}
tr.row-gestao td.rv{color:#c8dff8}
tr.row-ug{background:#2a4a7f;cursor:pointer}
tr.row-ug:hover{background:#243f6e}
tr.row-ug td{color:#dce8f8;font-weight:700;padding:9px 14px 9px 26px;font-size:12px;border-bottom:1.5px solid #1e3267;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.row-ug td:first-child{text-align:left}
tr.row-ug td.rv{color:#b8d4f4;font-size:12px}
tr.row-conta{background:#3a5a92;cursor:pointer}
tr.row-conta:hover{background:#324e80}
tr.row-conta td{color:#e2ecfa;font-weight:700;padding:8px 14px 8px 38px;font-size:11.5px;border-bottom:1px solid #1e3267;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.row-conta td:first-child{text-align:left}
tr.row-conta td.rv{color:#c4dbf6;font-size:11.5px}
tr.row-fonte{background:var(--surface)}
tr.row-fonte:nth-child(even){background:var(--row-alt)}
tr.row-fonte:hover{background:var(--hover)}
tr.row-fonte td{padding:8px 14px 8px 54px;font-size:12.5px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--text)}
tr.row-fonte td:first-child{text-align:left;color:var(--muted)}
tr.row-fonte td.rv{color:var(--navy);font-weight:600}
tfoot tr td{background:#f0f4fb;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;font-size:12.5px;text-align:right;white-space:nowrap}
tfoot tr td:first-child{text-align:left}
.tog{display:inline-block;width:16px;text-align:center;font-size:10px;opacity:.7}
.empty{text-align:center;color:var(--muted);padding:32px;font-size:13px}
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">🎯</div>
    <h1>DDR POR SALDO
      <span>SIGGO · Ano Exercício 2026</span>
    </h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <div id="ts">Gerado em: {timestamp}</div>
<div id="ldg" style="display:none;position:fixed;inset:0;background:rgba(13,27,62,.55);z-index:999;align-items:center;justify-content:center"><div style="background:#fff;border-radius:12px;padding:28px 36px;font-size:14px;color:#1a2033;box-shadow:0 8px 32px rgba(0,0,0,.25)">Carregando dados…</div></div>
</header>

<div class="fbar">
  <div class="fg ug-wrap" style="min-width:220px">
    <label>Unidade Gestora</label>
    <input class="ug-input" id="ug-inp" placeholder="Código ou nome..." autocomplete="off" oninput="ugInput()" onfocus="ugInput()">
    <button class="ug-clear" id="ug-clr" onclick="limparUG()">✕</button>
    <div class="ug-dd" id="ug-dd"></div>
  </div>
  <div class="fg">
    <label>Mês</label>
    <select id="fm" onchange="trocarMes(this.value)"><option value="">Todos</option></select>
  </div>
  <div class="fg">
    <label>Contas Contábeis</label>
    <select id="fgr" onchange="aplicar()">
      <option value="">Todos</option>
      <option value="622920101">Empenhos a Liquidar</option>
      <option value="622920102">Empenhos em Liquidação</option>
      <option value="622920103">Empenhos Liq. a Pagar</option>
      <option value="622920104">Empenhos Pagos</option>
      <option value="827110401">Obrigações Extraorçamentárias</option>
      <option value="721190300">Disponibilidade Real</option>
    </select>
  </div>
  <div class="fg">
    <label>Fonte</label>
    <select id="ff" onchange="aplicar()"><option value="">Todas</option></select>
  </div>
  <div class="fg">
    <label>Exibir saldos</label>
    <select id="fs" onchange="aplicar()">
      <option value="todos" selected>Todos</option>
      <option value="dif_nz">Com diferença</option>
      <option value="dif_pos">Diferença positiva</option>
      <option value="dif_neg">Diferença negativa</option>
    </select>
  </div>
  <div class="btns">
    <button class="btn btn-g" onclick="expandirTudo()">▼ Expandir tudo</button>
    <button class="btn btn-g" onclick="recolherTudo()">▲ Recolher tudo</button>
    <button class="btn btn-g" onclick="limpar()">↺ Limpar filtros</button>
    <button class="btn btn-p" onclick="exportar()">↓ Exportar CSV</button>
  </div>
</div>

<div class="kpi-section">
  <div class="kpi-body open" id="krow-total"></div>
  <div class="kpi-toggle" onclick="toggleKpis(this)">
    <span class="kpi-toggle-arrow">▶</span> Detalhar por grupo
  </div>
  <div class="kpi-body" id="krow"></div>
</div>
<div class="cnt-bar"><span id="cnt"></span></div>

<div class="tw">
  <table>
    <thead><tr>
      <th style="width:300px;text-align:left">Gestão / UG / Conta Contábil / Fonte</th>
      <th style="width:120px;text-align:left">Mês</th>
      <th style="width:190px">Saldos Contas (a)</th>
      <th style="width:150px">Saldos Contas (b)</th>
      <th style="width:130px">Diferença</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
    <tfoot><tr id="tfoot"></tr></tfoot>
  </table>
</div>

<script>
const DADOS_B64={dados_b64};
const UGS_B64='{ugs_b64}';


const MESES=['Saldo Inicial','Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro','Encerramento do Exercício','Encerramento do Exercício'];
const nomeMes=m=>{const n=Number(m);return(n>=0&&n<=14)?n+' · '+MESES[n]:String(m);};
let ALL=[],UGS={},CACHE={},mesSel='',fil=[],ugSel='';

async function decomp(b64){
  const bin=atob(b64),bytes=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
  const ds=new DecompressionStream('gzip');
  const writer=ds.writable.getWriter();
  writer.write(bytes);writer.close();
  const chunks=[];const reader=ds.readable.getReader();
  while(true){const{done,value}=await reader.read();if(done)break;chunks.push(value);}
  const total=chunks.reduce((s,c)=>s+c.length,0);
  const arr=new Uint8Array(total);let off=0;
  chunks.forEach(c=>{arr.set(c,off);off+=c.length;});
  return JSON.parse(new TextDecoder().decode(arr));
}
async function carregarMes(mes){
  if(CACHE[mes])return CACHE[mes];
  if(!DADOS_B64[mes])return[];
  const data=await decomp(DADOS_B64[mes]);
  CACHE[mes]=data;return data;
}
(async()=>{
  document.getElementById('ldg').style.display='flex';
  try{
    const[todosData,ugsData]=await Promise.all([decomp(DADOS_B64['']),decomp(UGS_B64)]);
    CACHE['']=todosData;ALL=todosData;UGS=ugsData;
  }catch(e){
    document.getElementById('ldg').innerHTML='<div style="background:#fff;border-radius:12px;padding:28px 36px;color:#c0392b">Erro ao carregar dados.</div>';return;
  }
  document.getElementById('ldg').style.display='none';
  initFiltros();
})();
async function trocarMes(mes){
  mesSel=mes;
  document.getElementById('ldg').style.display='flex';
  ALL=await carregarMes(mes);
  document.getElementById('ldg').style.display='none';
  aplicar();
}
const brl=v=>{if(v===null||v===undefined||isNaN(v))return'—';const r=Math.round(Number(v)*100)/100;return(r||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});};
const vc=v=>(v===null||v===undefined||isNaN(v))?'vz':Math.abs(v)<0.005?'vz':v>0?'vp':'vn';
const num=v=>(v===null||v===undefined||isNaN(v))?0:Number(v);

/* ── UG dropdown ── */
function ugInput(){
  const v=document.getElementById('ug-inp').value.trim().toLowerCase();
  const dd=document.getElementById('ug-dd');
  document.getElementById('ug-clr').style.display=v?'block':'none';
  if(!v){dd.style.display='none';ugSel='';aplicar();return;}
  const matches=Object.entries(UGS).filter(([k,n])=>k.includes(v)||n.toLowerCase().includes(v)).slice(0,30);
  if(!matches.length){dd.innerHTML='<div class="ug-dd-empty">Nenhuma UG encontrada</div>';dd.style.display='block';return;}
  dd.innerHTML=matches.map(([k,n])=>'<div class="ug-dd-item" onclick="selUG(\''+k+'\',\''+n.replace(/'/g,"\\'")+'\')" ><strong>'+k+'</strong> — '+n+'</div>').join('');
  dd.style.display='block';
}
function selUG(k,n){
  ugSel=k;
  document.getElementById('ug-inp').value=k+' — '+n;
  document.getElementById('ug-clr').style.display='block';
  document.getElementById('ug-dd').style.display='none';
  aplicar();
}
function limparUG(){ugSel='';document.getElementById('ug-inp').value='';document.getElementById('ug-clr').style.display='none';document.getElementById('ug-dd').style.display='none';aplicar();}
document.addEventListener('click',function(e){if(!e.target.closest('.ug-wrap'))document.getElementById('ug-dd').style.display='none';});

/* ── Filtros ── */
function aplicar(){
  const fgr=document.getElementById('fgr').value;
  const ff=document.getElementById('ff').value;
  const sd=document.getElementById('fs').value;
  fil=ALL.filter(r=>{
    if(ugSel&&String(r.COUG)!==ugSel)return false;
    if(fgr&&String(r.CONTA_ORCAMENTARIA)!==fgr)return false;
    if(ff&&String(r.COFONTE)!==ff)return false;
    const d=num(r.DIFERENCA);
    if(sd==='dif_pos'&&d<=0)return false;
    if(sd==='dif_neg'&&d>=0)return false;
    if(sd==='dif_nz'&&Math.abs(d)<0.005)return false;
    return true;
  });
  render();kpis();
}
function limpar(){document.getElementById('fm').value='';mesSel='';ALL=CACHE['']||[];document.getElementById('fgr').value='';document.getElementById('ff').value='';document.getElementById('fs').value='todos';limparUG();}

function initFiltros(){
  const meses=Object.keys(DADOS_B64).filter(k=>k!=='').sort((a,b)=>Number(a)-Number(b));
  const smEl=document.getElementById('fm');
  smEl.innerHTML='<option value="">Todos</option>';
  meses.forEach(m=>{const o=document.createElement('option');o.value=m;o.textContent=nomeMes(m);smEl.appendChild(o);});


  const fontes=[...new Set(ALL.map(r=>String(r.COFONTE)))].sort();
  const sf=document.getElementById('ff');
  sf.innerHTML='<option value="">Todas</option>';
  fontes.forEach(f=>{const o=document.createElement('option');o.value=f;o.textContent=f;sf.appendChild(o);});
  aplicar();
}

/* ── Helpers de soma e células ── */
function soma(arr){
  return{SALDO_ORCAMENTARIA:arr.reduce((s,r)=>s+num(r.SALDO_ORCAMENTARIA),0),
    SALDO_CONTROLE:arr.reduce((s,r)=>s+num(r.SALDO_CONTROLE),0),
    DIFERENCA:arr.reduce((s,r)=>s+num(r.DIFERENCA),0)};
}
function valCols(t){
  return '<td class="rv '+vc(t.SALDO_ORCAMENTARIA)+'">'+brl(t.SALDO_ORCAMENTARIA)+'</td>'
        +'<td class="rv '+vc(t.SALDO_CONTROLE)+'">'+brl(t.SALDO_CONTROLE)+'</td>'
        +'<td class="rv '+vc(t.DIFERENCA)+'">'+brl(t.DIFERENCA)+'</td>';
}

/* ── Render hierárquico: Gestao -> UG -> Conta Orcamentaria -> Fonte/Mes ── */
function render(){
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  if(!fil.length){
    tb.innerHTML='<tr><td colspan="5" class="empty">Nenhum registro encontrado.</td></tr>';
    tf.innerHTML='';document.getElementById('cnt').textContent='Nenhum registro';return;
  }

  const tree={};
  fil.forEach(r=>{
    const g=String(r.COGESTAO),u=String(r.COUG),c=String(r.CONTA_ORCAMENTARIA);
    if(!tree[g])tree[g]={};
    if(!tree[g][u])tree[g][u]={};
    if(!tree[g][u][c])tree[g][u][c]=[];
    tree[g][u][c].push(r);
  });

  const nF=fil.length,nU=new Set(fil.map(r=>r.COUG)).size,nC=new Set(fil.map(r=>r.CONTA_ORCAMENTARIA)).size,nG=Object.keys(tree).length;
  document.getElementById('cnt').textContent=nG+' Gestão/ões · '+nU+' UGs · '+nC+' contas · '+nF.toLocaleString('pt-BR')+' linha'+(nF!==1?'s':'');

  let html='';
  Object.keys(tree).sort().forEach(g=>{
    const ugMap=tree[g];
    const todosG=Object.values(ugMap).map(cm=>Object.values(cm).flat()).flat();
    const tg=soma(todosG);
    const nUgG=Object.keys(ugMap).length;
    const gid='g_'+g;
    html+='<tr class="row-gestao" onclick="toggle(\''+gid+'\')">'
         +'<td><span class="tog" id="tog_'+gid+'">▶</span> GESTÃO '+g
         +' <span style="font-size:10px;opacity:.55">('+nUgG+' UG'+(nUgG!==1?'s':'')+')</span></td>'
         +'<td></td>'+valCols(tg)+'</tr>';

    Object.keys(ugMap).sort((a,b)=>Number(a)-Number(b)).forEach(u=>{
      const contaMap=ugMap[u],nome=UGS[u]||'';
      const todosU=Object.values(contaMap).flat();
      const tu=soma(todosU);
      const nContasU=Object.keys(contaMap).length;
      const uid=gid+'_u'+u;
      html+='<tr class="row-ug" data-par="'+gid+'" style="display:none" onclick="toggle(\''+uid+'\')">'
           +'<td><span class="tog" id="tog_'+uid+'">▶</span> UG '+u+(nome?' · '+nome:'')
           +' <span style="font-size:10px;opacity:.5">('+nContasU+' conta'+(nContasU!==1?'s':'')+')</span></td>'
           +'<td></td>'+valCols(tu)+'</tr>';

      Object.keys(contaMap).sort().forEach(c=>{
        const fontes=contaMap[c],tc=soma(fontes);
        const cc=fontes[0].CONTA_CONTROLE||'—';
        const cid=uid+'_c'+c;
        html+='<tr class="row-conta" data-par="'+uid+'" style="display:none" onclick="toggle(\''+cid+'\')">'
             +'<td><span class="tog" id="tog_'+cid+'">▶</span> '+(NOME_CONTA[c]||c)
             +'<span class="acct">'+(ACCT_LABEL[c]||c+' (a) · '+cc+' (b)')+'</span></td>'
             +'<td></td>'+valCols(tc)+'</tr>';

        fontes.slice().sort((a,b)=>(Number(a.INMES)||0)-(Number(b.INMES)||0)||(String(a.COFONTE).localeCompare(String(b.COFONTE)))).forEach(r=>{
          html+='<tr class="row-fonte" data-par="'+cid+'" style="display:none">'
               +'<td>Fonte '+r.COFONTE+'</td>'
               +'<td style="text-align:left;color:var(--text)">'+(mesSel!==''?nomeMes(mesSel):'—')+'</td>'
               +'<td class="rv '+vc(r.SALDO_ORCAMENTARIA)+'">'+brl(r.SALDO_ORCAMENTARIA)+'</td>'
               +'<td class="rv '+vc(r.SALDO_CONTROLE)+'">'+brl(r.SALDO_CONTROLE)+'</td>'
               +'<td class="rv '+vc(r.DIFERENCA)+'">'+brl(r.DIFERENCA)+'</td>'
               +'</tr>';
        });
      });
    });
  });
  tb.innerHTML=html;

  const tot=soma(fil);
  tf.innerHTML='<td>Total Geral · '+nG+' Gestão/ões · '+nU+' UGs · '+nC+' contas · '+nF.toLocaleString('pt-BR')+' linhas</td>'
    +'<td></td>'
    +'<td class="'+vc(tot.SALDO_ORCAMENTARIA)+'">'+brl(tot.SALDO_ORCAMENTARIA)+'</td>'
    +'<td class="'+vc(tot.SALDO_CONTROLE)+'">'+brl(tot.SALDO_CONTROLE)+'</td>'
    +'<td class="'+vc(tot.DIFERENCA)+'">'+brl(tot.DIFERENCA)+'</td>';
}

/* ── Toggle ── */
function toggle(id){
  const tog=document.getElementById('tog_'+id);
  const aberto=tog&&tog.textContent.trim()==='▼';
  if(aberto){
    fecharDescendentes(id);
    if(tog)tog.textContent='▶';
  } else {
    document.querySelectorAll('[data-par="'+id+'"]').forEach(tr=>tr.style.display='');
    if(tog)tog.textContent='▼';
  }
}
function fecharDescendentes(id){
  document.querySelectorAll('[data-par="'+id+'"]').forEach(tr=>{
    tr.style.display='none';
    const t=tr.querySelector('.tog');
    if(t){
      if(t.textContent.trim()==='▼') t.textContent='▶';
      const tid=t.id.replace('tog_','');
      fecharDescendentes(tid);
    }
  });
}
function expandirTudo(){
  document.querySelectorAll('[data-par]').forEach(tr=>tr.style.display='');
  document.querySelectorAll('.tog').forEach(el=>el.textContent='▼');
}
function recolherTudo(){
  document.querySelectorAll('[data-par]').forEach(tr=>tr.style.display='none');
  document.querySelectorAll('.tog').forEach(el=>el.textContent='▶');
}

/* ── KPIs ── */
const NOME_CONTA={'622920101':'Empenhos a Liquidar','622920102':'Empenhos em Liquidação','622920103':'Empenhos Liq. a Pagar','622920104':'Empenhos Pagos','827110401':'Obrigações Extraorçamentárias','721190300':'Disponibilidade Real'};
const ACCT_LABEL={'622920101':'622920101 + 631100000 + 631810000 (a) · 821120100 (b)','622920102':'622920102 + 631200000 + 631820000 (a) · 821120200 (b)','622920103':'622920103 + 631300000 + 632110100 + 632110200 + 632110300 + 632110400 (a) · 821130200 + 821130100 (b)','622920104':'622920104 + 631400000 + 63221XXXX (a) · 821140000 (b)','827110401':'827110401 (a) · 821130300 (b)','721190300':'721190300 (a) · 821110100 + 821110200 + 821120100 (b)'};
const GRUPOS_ORDER=['622920101','622920102','622920103','622920104','827110401','721190300'];
function toggleKpis(el){
  const body=document.getElementById('krow');
  const arrow=el.querySelector('.kpi-toggle-arrow');
  const open=body.classList.toggle('open');
  arrow.classList.toggle('open',open);
}
function kpis(){
  const sm=c=>fil.reduce((a,r)=>a+num(r[c]),0);
  const sa=sm('SALDO_ORCAMENTARIA'),sb=sm('SALDO_CONTROLE'),dif=sm('DIFERENCA');
  const cd=fil.filter(r=>Math.abs(num(r.DIFERENCA))>=0.005).length;
  const pct=fil.length?((cd/fil.length)*100).toFixed(1):'0.0';
  const dcT=Math.abs(dif)<0.01?'ko':dif>0?'kw':'ka';
  /* Totais — sempre visíveis */
  document.getElementById('krow-total').innerHTML=
    '<div class="kpi-total-row">'
    +'<div class="kpi"><div class="kl">Total Geral (a)</div><div class="kv '+vc(sa)+'">'+brl(sa)+'</div></div>'
    +'<div class="kpi"><div class="kl">Total Geral (b)</div><div class="kv '+vc(sb)+'">'+brl(sb)+'</div></div>'
    +'<div class="kpi '+dcT+'"><div class="kl">Diferença Total (a−b)</div><div class="kv '+vc(dif)+'">'+brl(dif)+'</div>'
    +'<div class="ks"><span class="badge '+(cd>0?'br':'bg')+'">'+cd.toLocaleString('pt-BR')+' linhas c/ dif. · '+pct+'%</span></div></div>'
    +'</div>';
  /* Grupos — colapsáveis */
  let html='<div class="kpi-row">';
  GRUPOS_ORDER.forEach(key=>{
    const rows=fil.filter(r=>String(r.CONTA_ORCAMENTARIA)===key);
    if(!rows.length)return;
    const ga=rows.reduce((s,r)=>s+num(r.SALDO_ORCAMENTARIA),0);
    const gb=rows.reduce((s,r)=>s+num(r.SALDO_CONTROLE),0);
    const gd=rows.reduce((s,r)=>s+num(r.DIFERENCA),0);
    const dc=Math.abs(gd)<0.01?'ko':gd>0?'kw':'ka';
    html+='<div class="kpi-group">'
      +'<div class="kpi-group-title">'+(NOME_CONTA[key]||key)
      +'<span style="font-weight:400;margin-left:6px;opacity:.65;font-size:8.5px;text-transform:none">'+ACCT_LABEL[key]+'</span></div>'
      +'<div class="kpi-group-inner">'
      +'<div class="kpi"><div class="kl">Saldo (a)</div><div class="kv '+vc(ga)+'">'+brl(ga)+'</div></div>'
      +'<div class="kpi"><div class="kl">Saldo (b)</div><div class="kv '+vc(gb)+'">'+brl(gb)+'</div></div>'
      +'<div class="kpi '+dc+'"><div class="kl">Dif. (a−b)</div><div class="kv '+vc(gd)+'">'+brl(gd)+'</div></div>'
      +'</div></div>';
  });
  html+='</div>';
  document.getElementById('krow').innerHTML=html;
}

/* ── Exportar CSV ── */
function exportar(){
  if(!fil.length)return alert('Nenhum dado para exportar.');
  const cols=['COGESTAO','COUG','CONTA_ORCAMENTARIA','COFONTE','SALDO_ORCAMENTARIA','CONTA_CONTROLE','SALDO_CONTROLE','DIFERENCA'];
  const hdrs=['Gestao','Unidade Gestora','Conta Orcamentaria','Fonte','Saldo Orcamentaria','Conta Controle','Saldo Controle','Diferenca'];
  const linhas=[hdrs.join(';')].concat(fil.map(r=>cols.map(c=>typeof r[c]==='number'?String(r[c]).replace('.',','):(r[c]===null?'':r[c])).join(';')));
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),download:'disponibilidade_destinacao_recurso.csv'});
  a.click();URL.revokeObjectURL(a.href);
}

initFiltros();
</script>
</body>
</html>
"""

# ── Geração do HTML ─────────────────────────────────────────────────────────────
def _normalizar(registros):
    import math
    out = []
    for r in registros:
        row = {}
        for k, v in r.items():
            if v is None:
                row[k] = None
            elif hasattr(v, "__float__") and not isinstance(v, (str, bool)):
                try:
                    f = float(v)
                    row[k] = None if math.isnan(f) or math.isinf(f) else f
                except Exception:
                    row[k] = str(v)
            else:
                row[k] = str(v) if not isinstance(v, (int, float, bool, type(None))) else v
        out.append(row)
    return out

def _gzip_b64(data) -> str:
    j = json.dumps(data, ensure_ascii=False)
    return base64.b64encode(gzip.compress(j.encode("utf-8"), compresslevel=9)).decode("ascii")

def gerar_html(dados_por_mes: dict, ugs: dict) -> str:
    dados_b64 = {mes: _gzip_b64(_normalizar(recs)) for mes, recs in dados_por_mes.items()}
    ugs_b64   = _gzip_b64(ugs)
    html = HTML_TEMPLATE
    html = html.replace('{dados_b64}', json.dumps(dados_b64, ensure_ascii=False))
    html = html.replace('{ugs_b64}',   ugs_b64)
    html = html.replace('{timestamp}', datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    return html

# ── Publicação no GitHub ────────────────────────────────────────────────────────
def publicar_github(html: str) -> str:
    import base64, requests
    api = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{ARQUIVO_HTML}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    r = requests.get(api, headers=headers)
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {"message": f"chore: atualiza disponibilidade por destinacao de recurso — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
               "content": base64.b64encode(html.encode("utf-8")).decode(),
               "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha
    r2 = requests.put(api, headers=headers, json=payload)
    r2.raise_for_status()
    return f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}/{ARQUIVO_HTML}"

# ── Main ────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ug",      type=str, default=None)
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")

    if args.ug:
        filtro_ug_1 = "AND sc.COUG = :ug"
        filtro_ug_2 = "AND sc.COUG = :ug"
        filtro_ug_3 = "AND sc.COUG = :ug"
        params = {"ug": int(args.ug)}
    else:
        filtro_ug_1 = filtro_ug_2 = filtro_ug_3 = ""
        params = {}

    sql_base = (SQL.replace("{schema}", SCHEMA)
                   .replace("{filtro_ug_1}", filtro_ug_1)
                   .replace("{filtro_ug_2}", filtro_ug_2)
                   .replace("{filtro_ug_3}", filtro_ug_3))

    def run(filtro_mes=""):
        s = sql_base.replace("{filtro_mes}", filtro_mes)
        return pd.read_sql(s, conn, params=params if params else None)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Conectando ao Oracle...")
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Executando consulta Todos os meses...")
        df_todos = run("")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {len(df_todos):,} registros (Todos).")

        dados_por_mes = {"": df_todos.to_dict(orient="records")}
        for mes in range(15):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Mês {mes}...", end=" ", flush=True)
            df_mes = run(f"AND sc.INMES = {mes}")
            if not df_mes.empty:
                dados_por_mes[str(mes)] = df_mes.to_dict(orient="records")
                print(f"{len(df_mes):,} registros.")
            else:
                print("vazio, ignorado.")

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando nomes das UGs...")
        ugs_df = pd.read_sql(f"SELECT COUG, NOUG FROM {SCHEMA}VUNIDADEGESTORA", conn)
        ugs = {str(int(r.COUG)): r.NOUG for r in ugs_df.itertuples() if r.NOUG}
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {len(ugs):,} UGs carregadas.")

    html = gerar_html(dados_por_mes, ugs)
    out = Path(ARQUIVO_HTML)
    out.write_text(html, encoding="utf-8")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] HTML salvo: {ARQUIVO_HTML}")

    if not args.no_push:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Publicando no GitHub...")
        url = publicar_github(html)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Publicado com sucesso.\n  -> {url}")

    df_ref = df_todos
    dif_nz = int((df_ref["DIFERENCA"].abs() >= 0.005).sum())
    print(f"\n-- Resumo ---------------------------------------------------")
    print(f"  Registros (Todos)   : {len(df_ref):,}")
    print(f"  Meses com dados     : {len(dados_por_mes)-1}")
    print(f"  UGs                 : {df_ref['COUG'].nunique():,}")
    print(f"  Com diferenca != 0  : {dif_nz:,}")
    print(f"  Diferenca total     : R$ {float(df_ref['DIFERENCA'].sum()):,.2f}")
    print(f"-------------------------------------------------------------\n")

if __name__ == "__main__":
    main()
