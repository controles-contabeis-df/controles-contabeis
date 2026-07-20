"""
Disponibilidades por Lançamento — SIGGO/Oracle
Gera um arquivo HTML autocontido com os dados embutidos e publica no GitHub Pages.

Dependências:
    pip install oracledb pandas requests

Uso:
    python extrair_disponibilidades.py
    python extrair_disponibilidades.py --ug 10101
    python extrair_disponibilidades.py --no-push   (gera HTML sem publicar)
"""

import argparse
import base64
import gzip
import json
import sys
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

SCHEMA = "MIL2026."

# ── GitHub ─────────────────────────────────────────────────────────────────────
GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
GITHUB_USER   = os.environ["GITHUB_USER"]
GITHUB_REPO   = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = os.environ["GITHUB_BRANCH"]

# Arquivo que será publicado no repositório
ARQUIVO_HTML = "disponibilidades_lancamento.html"

# ── SQL ────────────────────────────────────────────────────────────────────────
SQL = """
WITH contas_permitidas AS (
    SELECT DISTINCT COCONTACONTABIL
    FROM {schema}VCONTACONTABIL
    WHERE INSISCONTABIL IN ('F', 'C', 'O')
),
lb AS (
    SELECT
        l.COGESTAOCONTAB AS COGESTAO,
        l.COUGCONTAB     AS COUG,
        l.COGESTAO       AS COGESTAO_EMIT,
        l.COUG           AS COUG_EMIT,
        l.INMES, l.COCONTACONTABIL,
        l.NUDOCUMENTO, l.COEVENTO, l.INDEBITOCREDITO,
        SUM(l.VALANCAMENTO) AS VALANCAMENTO,
        MIN(l.DALANCAMENTO) AS DALANCAMENTO
    FROM {schema}VLANCAMENTOCONTABIL l
    INNER JOIN contas_permitidas cp
        ON l.COCONTACONTABIL = cp.COCONTACONTABIL
    {filtro_ug}
    GROUP BY
        l.COGESTAOCONTAB, l.COUGCONTAB,
        l.COGESTAO, l.COUG,
        l.INMES, l.COCONTACONTABIL,
        l.NUDOCUMENTO, l.COEVENTO, l.INDEBITOCREDITO
),
ev_roteiro AS (
    -- Eventos cujo impacto liquido na formula (AF-PF-RPNP-721190300) e diferente de zero,
    -- conforme definido no EVENTOROTEIRO. So esses "abrem" a equacao por definicao contabil.
    SELECT er.COEVENTO
    FROM {schema}EVENTOROTEIRO er
    GROUP BY er.COEVENTO
    HAVING SUM(CASE
        WHEN REGEXP_LIKE(TRIM(er.COCONTACONTABIL), '^[0-9]{{9}}$')
         AND TO_NUMBER(TRIM(er.COCONTACONTABIL)) BETWEEN 100000000 AND 199999999
        THEN CASE er.INDEBITOCREDITO WHEN 'D' THEN 1 ELSE -1 END
        WHEN REGEXP_LIKE(TRIM(er.COCONTACONTABIL), '^[0-9]{{9}}$')
         AND TO_NUMBER(TRIM(er.COCONTACONTABIL)) BETWEEN 200000000 AND 229999999
        THEN CASE er.INDEBITOCREDITO WHEN 'C' THEN -1 ELSE 1 END
        WHEN TRIM(er.COCONTACONTABIL) = '631100000'
        THEN CASE er.INDEBITOCREDITO WHEN 'C' THEN -1 ELSE 1 END
        WHEN TRIM(er.COCONTACONTABIL) = '721190300'
        THEN CASE er.INDEBITOCREDITO WHEN 'D' THEN -1 ELSE 1 END
        ELSE 0
    END) <> 0
),
ev_caus AS (
    -- Eventos que (a) abrem a equacao no roteiro E (b) tocaram efetivamente
    -- uma conta da formula neste documento/UG especifico.
    -- Isso elimina eventos que coexistem no documento mas nao contribuem para a diferenca.
    SELECT COGESTAO, COUG, COGESTAO_EMIT, COUG_EMIT, INMES, NUDOCUMENTO,
           LISTAGG(TO_CHAR(COEVENTO), ', ') WITHIN GROUP (ORDER BY COEVENTO) AS COEVENTO
    FROM (
        SELECT DISTINCT lb.COGESTAO, lb.COUG, lb.COGESTAO_EMIT, lb.COUG_EMIT,
                        lb.INMES, lb.NUDOCUMENTO, lb.COEVENTO
        FROM lb
        INNER JOIN ev_roteiro er ON er.COEVENTO = lb.COEVENTO
        WHERE (
            (lb.COCONTACONTABIL BETWEEN 100000000 AND 199999999)
            OR (lb.COCONTACONTABIL BETWEEN 200000000 AND 229999999)
            OR lb.COCONTACONTABIL = 631100000
            OR lb.COCONTACONTABIL = 721190300
        )
    )
    GROUP BY COGESTAO, COUG, COGESTAO_EMIT, COUG_EMIT, INMES, NUDOCUMENTO
),
doc_tot AS (
    SELECT
        COGESTAO AS GESTAO, COUG AS UNIDADE_GESTORA, COGESTAO_EMIT, COUG_EMIT, INMES, NUDOCUMENTO,
        TO_CHAR(MIN(DALANCAMENTO), 'DD/MM/YYYY') AS DALANCAMENTO,
        ROUND(SUM(CASE WHEN COCONTACONTABIL BETWEEN 100000000 AND 199999999
                       THEN CASE INDEBITOCREDITO WHEN 'D' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                       ELSE 0 END), 2) AS AF,
        ROUND(SUM(CASE WHEN COCONTACONTABIL BETWEEN 200000000 AND 229999999
                       THEN CASE INDEBITOCREDITO WHEN 'C' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                       ELSE 0 END), 2) AS PF,
        ROUND(SUM(CASE WHEN COCONTACONTABIL = 631100000
                       THEN CASE INDEBITOCREDITO WHEN 'C' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                       ELSE 0 END), 2) AS RPNP,
        ROUND(SUM(CASE WHEN COCONTACONTABIL = 721190300
                       THEN CASE INDEBITOCREDITO WHEN 'D' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                       ELSE 0 END), 2) AS CONTA_721190300,
        ROUND(
            SUM(CASE WHEN COCONTACONTABIL BETWEEN 100000000 AND 199999999
                     THEN CASE INDEBITOCREDITO WHEN 'D' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                     ELSE 0 END)
          - SUM(CASE WHEN COCONTACONTABIL BETWEEN 200000000 AND 229999999
                     THEN CASE INDEBITOCREDITO WHEN 'C' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                     ELSE 0 END)
          - SUM(CASE WHEN COCONTACONTABIL = 631100000
                     THEN CASE INDEBITOCREDITO WHEN 'C' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                     ELSE 0 END)
        , 2) AS AF_MENOS_PF_RPNP,
        ROUND(
            SUM(CASE WHEN COCONTACONTABIL BETWEEN 100000000 AND 199999999
                     THEN CASE INDEBITOCREDITO WHEN 'D' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                     ELSE 0 END)
          - SUM(CASE WHEN COCONTACONTABIL BETWEEN 200000000 AND 229999999
                     THEN CASE INDEBITOCREDITO WHEN 'C' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                     ELSE 0 END)
          - SUM(CASE WHEN COCONTACONTABIL = 631100000
                     THEN CASE INDEBITOCREDITO WHEN 'C' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                     ELSE 0 END)
          - SUM(CASE WHEN COCONTACONTABIL = 721190300
                     THEN CASE INDEBITOCREDITO WHEN 'D' THEN VALANCAMENTO ELSE -VALANCAMENTO END
                     ELSE 0 END)
        , 2) AS DIFERENCA_AF_B
    FROM lb
    GROUP BY COGESTAO, COUG, COGESTAO_EMIT, COUG_EMIT, INMES, NUDOCUMENTO
)
SELECT
    d.GESTAO, d.UNIDADE_GESTORA, d.COGESTAO_EMIT, d.COUG_EMIT, d.INMES, d.NUDOCUMENTO,
    d.DALANCAMENTO,
    CASE WHEN ABS(d.DIFERENCA_AF_B) >= 0.01 THEN NVL(ec.COEVENTO, '—') ELSE '—' END AS COEVENTO,
    d.AF, d.PF, d.RPNP, d.CONTA_721190300, d.AF_MENOS_PF_RPNP, d.DIFERENCA_AF_B
FROM doc_tot d
LEFT JOIN ev_caus ec
       ON ec.COGESTAO      = d.GESTAO
      AND ec.COUG          = d.UNIDADE_GESTORA
      AND ec.COGESTAO_EMIT = d.COGESTAO_EMIT
      AND ec.COUG_EMIT     = d.COUG_EMIT
      AND ec.INMES         = d.INMES
      AND ec.NUDOCUMENTO   = d.NUDOCUMENTO
WHERE ABS(d.AF) + ABS(d.PF) + ABS(d.RPNP) + ABS(d.CONTA_721190300) > 0
ORDER BY d.GESTAO, d.UNIDADE_GESTORA, d.COGESTAO_EMIT, d.COUG_EMIT, d.INMES, d.NUDOCUMENTO
"""

# ── HTML template ──────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<!-- v-plana-paginada -->
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Disponibilidades por Lançamento</title>
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
  /* Paleta de identação — do mais escuro ao mais claro */
  --g-bg:#404142;      /* Gestão — carvão escuro, contraste máximo */
  --g-bg-h:#4f5052;
  --ug-bg:#4A6075;     /* UG — azul-acinzentado médio */
  --ug-bg-h:#587085;
  --lv-pos:#7de8b0;    /* Verde claro legível sobre fundo escuro */
  --lv-neg:#ff8f82;    /* Salmão legível sobre fundo escuro */
  --lv-zero:rgba(255,255,255,.42);
  /* Botão de destaque */
  --blue-btn:#487AA8;
}
body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}
header{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}
.hlogo{width:32px;height:32px;background:var(--teal);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}
header h1{font-size:14px;font-weight:700;letter-spacing:.6px;text-transform:uppercase}
header h1 span{font-weight:400;color:#9ab0cc;font-size:12px;display:block;text-transform:none;letter-spacing:0;margin-top:1px}
#ts{font-size:11px;color:#7a99bb;white-space:nowrap}
.fbar{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end}
.fg{display:flex;flex-direction:column;gap:4px}
.fg label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.fg select{border:1.5px solid var(--border);border-radius:6px;padding:7px 28px 7px 10px;font-size:12.5px;min-width:170px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E") no-repeat right 9px center;color:var(--text);cursor:pointer;appearance:none}
.fg select:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ug-wrap{position:relative;min-width:260px}
.ug-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text);transition:border-color .15s}
.ug-input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ug-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}
.ug-dd{position:absolute;top:calc(100% + 4px);left:0;right:0;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:240px;overflow-y:auto;display:none}
.ug-dd-item{padding:8px 12px;cursor:pointer;font-size:12.5px;border-bottom:1px solid var(--border);transition:background .1s}
.ug-dd-item:last-child{border-bottom:none}
.ug-dd-item:hover{background:var(--hover)}
.ug-dd-item strong{color:var(--navy);font-weight:700}
.ug-dd-empty{padding:12px;color:var(--muted);font-size:12px;text-align:center}
.bgrp{display:flex;gap:8px;margin-left:auto;align-items:flex-end}
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;transition:filter .15s,transform .1s;white-space:nowrap}
.btn:hover{filter:brightness(1.08);transform:translateY(-1px)}
.btn-p{background:var(--teal);color:#fff}
.btn-g{background:var(--border);color:var(--text)}
.btn-r{background:var(--red);color:#fff}
.btn-b{background:var(--blue-btn);color:#fff}
/* Dropdown de Eventos Potenciais */
.ev-drop{position:relative}
.ev-cnt-badge{background:rgba(255,255,255,.22);border-radius:10px;padding:1px 7px;font-size:10px;margin-left:5px;font-weight:700}
.ev-popup{position:absolute;right:0;top:calc(100% + 8px);z-index:400;width:460px;background:#fff;border:1.5px solid var(--border);border-radius:var(--radius);box-shadow:0 8px 28px rgba(0,0,0,.18);display:none;overflow:hidden}
.ev-popup.open{display:block}
.ev-popup-hdr{padding:10px 16px;font-weight:700;font-size:12px;color:var(--navy);border-bottom:1px solid var(--border);background:#f0f4fb;display:flex;justify-content:space-between;align-items:center}
.ev-popup-hdr span{color:var(--muted);font-weight:400;font-size:11px}
.ev-popup-body{max-height:400px;overflow-y:auto;overflow-x:visible}
.ev-popup-foot{padding:8px 12px;border-top:1px solid var(--border);display:flex;justify-content:flex-end;background:#fafbfd}
/* KPIs */
.krow{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;padding:18px 28px 4px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}
.kpi.kw::before{background:linear-gradient(90deg,#f0a500,#ffcc44)}
.kpi.ka::before{background:linear-gradient(90deg,var(--red),#e74c3c)}
.kpi.ko::before{background:linear-gradient(90deg,var(--green),#27ae60)}
.kl{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.kv{font-size:17px;font-weight:700;letter-spacing:-.3px;line-height:1}
.ks{font-size:11px;color:var(--muted);margin-top:5px}
/* Painel de Eventos */
.ev-sec{margin:16px 28px 0}
.ev-card{background:var(--surface);border:1.5px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);overflow:hidden}
.ev-hdr{display:flex;align-items:center;gap:10px;padding:12px 18px;cursor:pointer;user-select:none;background:linear-gradient(90deg,#f0f4fb,#fff);transition:background .12s}
.ev-hdr:hover{background:#e8eef8}
.ev-title{font-size:12.5px;font-weight:700;color:var(--navy);flex:1}
.ev-badge{font-size:10px;font-weight:700;border-radius:20px;padding:2px 10px;letter-spacing:.3px;background:#fde8e6;color:var(--red)}
.ev-badge.ok{background:#e6f5ec;color:var(--green)}
.ev-arrow{font-size:11px;color:var(--muted);transition:transform .2s}
.ev-arrow.open{transform:rotate(180deg)}
.ev-body{display:none}
.ev-body.open{display:block}
.ev-tbl{width:100%;border-collapse:collapse}
.ev-tbl th{background:#f0f4fb;color:var(--muted);font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;padding:8px 14px;text-align:right;border-bottom:1px solid var(--border)}
.ev-tbl th:first-child{text-align:left}
.ev-tbl td{padding:8px 14px;border-bottom:1px solid var(--border);font-size:12.5px;text-align:right;font-variant-numeric:tabular-nums}
.ev-tbl td:first-child{font-family:'Consolas','Courier New',monospace;font-size:13px;font-weight:700;color:var(--navy);text-align:left;white-space:normal;word-break:break-word}
.ev-tbl tr:last-child td{border-bottom:none}
.ev-tbl tr:hover td{background:var(--hover)}
.ev-foot{display:flex;justify-content:flex-end;padding:10px 14px;border-top:1px solid var(--border);background:#fafbfd}
/* Tabela principal */
.tsec{padding:16px 28px 32px}
.thead-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.ttitle{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.tctrl{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.sw{position:relative}
.sw input{border:2px solid var(--teal);border-radius:6px;padding:8px 12px 8px 34px;font-size:13px;width:260px;background:#fff;box-shadow:0 0 0 3px rgba(0,144,168,.08)}
.sw input:focus{outline:none;border-color:var(--navy);box-shadow:0 0 0 3px rgba(0,144,168,.18)}
.sw::before{content:'🔍';position:absolute;left:9px;top:50%;transform:translateY(-50%);font-size:13px;pointer-events:none}
.tw{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}
table{width:100%;border-collapse:collapse;table-layout:fixed;min-width:1100px}
thead th{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:right;white-space:nowrap;user-select:none;letter-spacing:.3px;overflow:hidden;text-overflow:ellipsis}
thead th:first-child{text-align:left}
.sort-icon{display:inline-block;width:14px;text-align:center;opacity:.55;font-size:10px}
/* Linhas de dados */
tr.dr{background:var(--surface)}
tr.dr.alt{background:var(--row-alt)}
tr.dr td{padding:8px 14px;border-bottom:1px solid var(--border);font-size:12.5px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis}
tr.dr td:first-child{text-align:left}
tr.dr:hover td{background:var(--hover)}
td.id-cell{line-height:1.35}
td.id-cell .id-main{font-size:12px;font-weight:600;color:var(--text)}
td.id-cell .id-sub{font-size:10.5px;color:var(--muted)}
td.mono{font-family:'Consolas','Courier New',monospace;font-size:12px;color:var(--muted);text-align:left}
.aviso-limite{background:#fff8e1;border:1px solid #f0c040;border-radius:6px;padding:8px 16px;font-size:12px;color:#7a5800;margin-bottom:10px;display:none}
tfoot td{background:#e8f0f8;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;font-size:12.5px;text-align:right}
tfoot td:first-child{text-align:left}
.empty{text-align:center;padding:56px;color:var(--muted)}
.vp{color:var(--green);font-weight:600}
.vn{color:var(--red);font-weight:600}
.vz{color:var(--muted)}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase}
.br{background:#fde8e6;color:var(--red)}.bg{background:#e6f5ec;color:var(--green)}
</style>
</head>
<body>
<div id="ldg" style="position:fixed;inset:0;background:#0d1b3e;color:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:'Segoe UI',sans-serif;font-size:18px;z-index:9999">
  <div style="font-size:40px;margin-bottom:16px">📊</div>
  <div>Carregando dados…</div>
  <div style="margin-top:8px;font-size:13px;color:#aac">Disponibilidades por Lançamento</div>
</div>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">📊</div>
    <h1>Disponibilidades por Lançamento<span>SIGGO · Ano Exercício 2026</span></h1>
  </div>
  <span id="ts">Gerado em: {timestamp}</span>
</header>
<div class="fbar">
  <div class="fg"><label>Gestão</label><select id="fg"><option value="">Todas</option></select></div>
  <div class="fg">
    <label>Unidade Gestora</label>
    <div class="ug-wrap">
      <input id="fu-input" class="ug-input" type="text" placeholder="Código ou nome da UG…" autocomplete="off"
             oninput="onUGInput()" onfocus="onUGFocus()" onblur="onUGBlur()">
      <button class="ug-clear" id="fu-clear" onclick="limparUG()" title="Limpar">✕</button>
      <div class="ug-dd" id="fu-dd"></div>
    </div>
  </div>
  <div class="fg"><label>Mês</label><select id="fm"><option value="">Todos</option></select></div>
  <div class="fg">
    <label>Exibir saldos</label>
    <select id="fs">
      <option value="todos">Todos</option>
      <option value="dif_pos">Diferença positiva (a−b &gt; 0)</option>
      <option value="dif_neg">Diferença negativa (a−b &lt; 0)</option>
      <option value="dif_nz">Apenas com diferença (a−b ≠ 0)</option>
    </select>
  </div>
  <div class="bgrp">
    <div class="ev-drop">
      <button class="btn btn-b" onclick="toggleEv(event)">📋 Eventos potenciais<span class="ev-cnt-badge" id="ev-cnt">0</span></button>
      <div class="ev-popup" id="ev-popup">
        <div class="ev-popup-hdr">Eventos potenciais causadores <span id="ev-popup-sub"></span></div>
        <div class="ev-popup-body">
          <table class="ev-tbl" style="table-layout:fixed;width:100%">
            <thead><tr><th style="width:42%">Evento</th><th style="width:18%;text-align:right">QTD.</th><th style="width:40%;text-align:right">DIF. ACUM.</th></tr></thead>
            <tbody id="ev-tbody"></tbody>
          </table>
        </div>
        <div class="ev-popup-foot">
          <button class="btn btn-b" onclick="exportarEventos()">⬇ Exportar CSV</button>
        </div>
      </div>
    </div>
    <button class="btn btn-r" onclick="filtrarDif()">⚠ Somente Diferenças</button>
    <button class="btn btn-g" onclick="limpar()">↺ Limpar filtros</button>
    <button class="btn btn-p" onclick="exportar()">⬇ Exportar CSV</button>
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
      <div class="sw"><input id="busca" type="text" placeholder="Buscar por nº documento ou evento…" oninput="aplicar()"></div>
    </div>
  </div>
  <div class="aviso-limite" id="aviso-limite"></div>
  <div class="tw">
    <table>
      <thead><tr id="thead-row">
        <th style="width:200px;text-align:left;cursor:pointer" onclick="sortBy('UG')">Gestão · UG <span id="s_UG" class="sort-icon">⇅</span></th>
        <th style="width:120px;cursor:pointer" onclick="sortBy('AF')">AF <span id="s_AF" class="sort-icon">⇅</span></th>
        <th style="width:120px;cursor:pointer" onclick="sortBy('PF')">PF <span id="s_PF" class="sort-icon">⇅</span></th>
        <th style="width:100px;cursor:pointer" onclick="sortBy('RPNP')">RPNP <span id="s_RPNP" class="sort-icon">⇅</span></th>
        <th style="width:130px;cursor:pointer" onclick="sortBy('AF_MENOS_PF_RPNP')">AF−(PF+RPNP) (a) <span id="s_AF_MENOS_PF_RPNP" class="sort-icon">⇅</span></th>
        <th style="width:130px;cursor:pointer" onclick="sortBy('CONTA_721190300')">Conta 721190300 (b) <span id="s_CONTA_721190300" class="sort-icon">⇅</span></th>
        <th style="width:130px;cursor:pointer" onclick="sortBy('DIFERENCA_AF_B')">Diferença (a−b) <span id="s_DIFERENCA_AF_B" class="sort-icon">⇅</span></th>
        <th style="width:95px;cursor:pointer" onclick="sortBy('DALANCAMENTO')">Data Lanç. <span id="s_DALANCAMENTO" class="sort-icon">⇅</span></th>
        <th style="width:115px;text-align:left">Gestão-UG Emitente</th>
        <th style="width:110px;cursor:pointer" onclick="sortBy('NUDOCUMENTO')">Nº Documento <span id="s_NUDOCUMENTO" class="sort-icon">⇅</span></th>
        <th style="width:120px;text-align:left">Evento</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
      <tfoot id="tfoot"></tfoot>
    </table>
  </div>
</div>
<div id="pag" style="display:flex;justify-content:center;align-items:center;gap:6px;padding:14px 28px;flex-wrap:wrap"></div>
<script>
const DADOS_B64='{dados}';
const UGS_B64='{ugs}';
let ALL=[],fil=[],ugSel='',emitSel='',pg=1,sortCol='',sortDir=1;
let ugList=[],emitList=[];
const fmtGestao=v=>String(v).padStart(5,'0');
const PS=200;
const MESES=['Saldo Inicial','Janeiro','Fevereiro','Mar\xe7o','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro','Encerramento do Exerc\xedcio','Encerramento do Exerc\xedcio'];
const brl=v=>isNaN(v)?'—':Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});
const vc=v=>Math.abs(v)<0.005?'vz':v>0?'vp':'vn';
function onUGInput(){
  const v=document.getElementById('fu-input').value.toLowerCase();
  const m=v?ugList.filter(u=>u.c.includes(v)||u.n.toLowerCase().includes(v)):ugList;
  renderDd(m);if(!v){ugSel='';document.getElementById('fu-clear').style.display='none';}
}
function onUGFocus(){const v=document.getElementById('fu-input').value.toLowerCase();renderDd(v?ugList.filter(u=>u.c.includes(v)||u.n.toLowerCase().includes(v)):ugList);}
function onUGBlur(){setTimeout(()=>document.getElementById('fu-dd').style.display='none',200);}
function renderDd(lista){
  const dd=document.getElementById('fu-dd');
  if(!lista.length){dd.innerHTML='<div class="ug-dd-empty">Nenhuma UG encontrada</div>';dd.style.display='block';return;}
  dd.innerHTML=lista.slice(0,80).map(u=>'<div class="ug-dd-item" onmousedown="selUG(\''+u.c+'\',\''+u.n.replace(/'/g,"\\'")+'\')">'+'<strong>'+u.c+'</strong>'+(u.n?' — '+u.n:'')+'</div>').join('');
  dd.style.display='block';
}
function selUG(c,n){ugSel=c;document.getElementById('fu-input').value=n?c+' — '+n:c;document.getElementById('fu-dd').style.display='none';document.getElementById('fu-clear').style.display='block';aplicar();}
function limparUG(){ugSel='';document.getElementById('fu-input').value='';document.getElementById('fu-clear').style.display='none';aplicar();}
function init(){
  const ugCodigosDados=[...new Set(ALL.map(r=>String(r.UNIDADE_GESTORA)))];
  ugList=ugCodigosDados.map(c=>{const n=UGS[c]||'';return{c,n}}).sort((a,b)=>a.c.localeCompare(b.c));
  emitList=[...new Set(ALL.map(r=>fmtGestao(r.COGESTAO_EMIT)+'-'+r.COUG_EMIT))].sort();
  const gestoes=[...new Set(ALL.map(r=>String(r.GESTAO)))].sort();
  const sgEl=document.getElementById('fg');
  sgEl.innerHTML='<option value="">Todos</option>';
  gestoes.forEach(g=>{const o=document.createElement('option');o.value=g;o.textContent=fmtGestao(g);sgEl.appendChild(o);});
  const meses=[...new Set(ALL.map(r=>r.INMES))].sort((a,b)=>a-b);
  const s=document.getElementById('fm');
  s.innerHTML='<option value="">Todos</option>';
  meses.forEach(m=>{const o=document.createElement('option');o.value=m;o.textContent=m+(MESES[m]?' — '+MESES[m]:'');s.appendChild(o)});
  aplicar();
}
function fill(id,vals){
  const s=document.getElementById(id),p=s.value;
  s.innerHTML='<option value="">Todos</option>';
  vals.forEach(v=>{const o=document.createElement('option');o.value=o.textContent=v;s.appendChild(o)});s.value=p;
}
function emitInput(){
  const v=document.getElementById('emit-in').value.toLowerCase();
  const m=v?emitList.filter(e=>e.includes(v)):emitList;
  renderEmitDd(m);
  if(!v){emitSel='';document.getElementById('emit-clr').style.display='none';aplicar();}
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
function aplicar(){
  const g=document.getElementById('fg').value,fm=document.getElementById('fm').value;
  const sd=document.getElementById('fs').value,b=document.getElementById('busca').value.trim().toLowerCase();
  fil=ALL.filter(r=>{
    if(g&&r.GESTAO!=g)return false;
    if(fm&&String(r.INMES)!=fm)return false;
    if(ugSel&&String(r.UNIDADE_GESTORA)!=ugSel)return false;
    if(emitSel&&(fmtGestao(r.COGESTAO_EMIT)+'-'+r.COUG_EMIT)!==emitSel)return false;
    if(sd==='dif_pos'&&r.DIFERENCA_AF_B<=0)return false;
    if(sd==='dif_neg'&&r.DIFERENCA_AF_B>=0)return false;
    if(sd==='dif_nz'&&Math.abs(r.DIFERENCA_AF_B)<0.005)return false;
    if(b&&!String(r.NUDOCUMENTO).toLowerCase().includes(b)&&!String(r.COEVENTO).toLowerCase().includes(b))return false;
    return true;
  });
  pg=1;render();kpis();renderEventos();
}
function filtrarDif(){document.getElementById('fg').value='';document.getElementById('fm').value='';document.getElementById('fs').value='dif_nz';document.getElementById('busca').value='';limparEmit();limparUG();}
function limpar(){document.getElementById('fg').value='';document.getElementById('fm').value='';document.getElementById('fs').value='todos';document.getElementById('busca').value='';limparEmit();limparUG();}
const SORT_COLS={'AF':'AF','PF':'PF','RPNP':'RPNP','AF_MENOS_PF_RPNP':'AF_MENOS_PF_RPNP','CONTA_721190300':'CONTA_721190300','DIFERENCA_AF_B':'DIFERENCA_AF_B','DALANCAMENTO':'DALANCAMENTO','NUDOCUMENTO':'NUDOCUMENTO','UG':'UNIDADE_GESTORA'};
function sortBy(col){
  if(sortCol===col){sortDir*=-1;}else{sortCol=col;sortDir=(col==='DIFERENCA_AF_B')?-1:1;}
  document.querySelectorAll('[id^="s_"]').forEach(el=>el.textContent='⇅');
  const sp=document.getElementById('s_'+col);if(sp)sp.textContent=sortDir>0?'↑':'↓';
  const field=SORT_COLS[col]||col;
  fil.sort((a,b)=>{
    const av=a[field],bv=b[field];
    if(typeof av==='number')return sortDir*(av-bv);
    if(field==='DALANCAMENTO'){
      const pa=av?av.split('/').reverse().join(''):'';
      const pb=bv?bv.split('/').reverse().join(''):'';
      return sortDir*(pa<pb?-1:pa>pb?1:0);
    }
    return sortDir*String(av||'').localeCompare(String(bv||''),'pt-BR');
  });
  pg=1;render();
}
function render(){
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  const n=fil.length;
  document.getElementById('cnt').textContent=n.toLocaleString('pt-BR')+' lançamento'+(n!==1?'s':'');
  if(!n){tb.innerHTML='<tr><td colspan="11" class="empty">Nenhum lançamento encontrado.</td></tr>';tf.innerHTML='';document.getElementById('pag').innerHTML='';return;}
  const s=(pg-1)*PS,rows=fil.slice(s,s+PS);
  let html='';
  rows.forEach((r,i)=>{
    const k=String(r.UNIDADE_GESTORA),nome=UGS[k]||'';
    const ugLabel='GEST\xc3O '+fmtGestao(r.GESTAO)+' \xb7 UG '+k+(nome?' – '+nome:'');
    const emitLabel=fmtGestao(r.COGESTAO_EMIT)+'-'+r.COUG_EMIT;
    html+='<tr class="dr'+(i%2?' alt':'')+'"><td style="text-align:left" title="'+ugLabel+'">'+ugLabel+'</td>'
      +'<td class="'+vc(r.AF)+'">'+brl(r.AF)+'</td>'
      +'<td class="'+vc(r.PF)+'">'+brl(r.PF)+'</td>'
      +'<td class="'+vc(r.RPNP)+'">'+brl(r.RPNP)+'</td>'
      +'<td class="'+vc(r.AF_MENOS_PF_RPNP)+'">'+brl(r.AF_MENOS_PF_RPNP)+'</td>'
      +'<td class="'+vc(r.CONTA_721190300)+'">'+brl(r.CONTA_721190300)+'</td>'
      +'<td class="'+vc(r.DIFERENCA_AF_B)+'">'+brl(r.DIFERENCA_AF_B)+'</td>'
      +'<td class="mono" style="white-space:nowrap">'+(r.DALANCAMENTO||'')+'</td>'
      +'<td class="mono" style="text-align:left">'+emitLabel+'</td>'
      +'<td class="mono">'+r.NUDOCUMENTO+'</td>'
      +'<td class="mono" style="white-space:normal;word-break:break-word;text-align:left">'+r.COEVENTO+'</td></tr>';
  });
  tb.innerHTML=html;
  const sm=c=>fil.reduce((a,r)=>a+r[c],0);
  const ugs=new Set(fil.map(r=>r.UNIDADE_GESTORA)).size;
  tf.innerHTML='<tr><td>Totais \xb7 '+n.toLocaleString('pt-BR')+' lançamentos \xb7 '+ugs+' UGs</td>'
    +'<td class="'+vc(sm('AF'))+'">'+brl(sm('AF'))+'</td>'
    +'<td class="'+vc(sm('PF'))+'">'+brl(sm('PF'))+'</td>'
    +'<td class="'+vc(sm('RPNP'))+'">'+brl(sm('RPNP'))+'</td>'
    +'<td class="'+vc(sm('AF_MENOS_PF_RPNP'))+'">'+brl(sm('AF_MENOS_PF_RPNP'))+'</td>'
    +'<td class="'+vc(sm('CONTA_721190300'))+'">'+brl(sm('CONTA_721190300'))+'</td>'
    +'<td class="'+vc(sm('DIFERENCA_AF_B'))+'">'+brl(sm('DIFERENCA_AF_B'))+'</td>'
    +'<td></td><td></td><td></td><td></td></tr>';
  paginar();
}
function paginar(){
  const pag=document.getElementById('pag'),pages=Math.ceil(fil.length/PS);
  if(pages<=1){pag.innerHTML='';return;}
  const s=(pg-1)*PS+1,e=Math.min(pg*PS,fil.length);
  let b='<button onclick="ir(pg-1)" '+(pg===1?'disabled':'')+' style="padding:4px 10px;border:1px solid var(--border);border-radius:5px;cursor:pointer;background:#fff">‹</button>';
  for(let i=1;i<=pages;i++){
    if(i===1||i===pages||Math.abs(i-pg)<=1)
      b+='<button onclick="ir('+i+')" style="padding:4px 10px;border:1px solid var(--border);border-radius:5px;cursor:pointer;background:'+(i===pg?'var(--teal)':'#fff')+';color:'+(i===pg?'#fff':'inherit')+'">'+i+'</button>';
    else if(Math.abs(i-pg)===2)
      b+='<button disabled style="border:none;background:none;padding:4px 6px">…</button>';
  }
  b+='<button onclick="ir(pg+1)" '+(pg===pages?'disabled':'')+' style="padding:4px 10px;border:1px solid var(--border);border-radius:5px;cursor:pointer;background:#fff">›</button>';
  pag.innerHTML='<span style="font-size:12px;color:var(--muted)">Mostrando '+s.toLocaleString('pt-BR')+'–'+e.toLocaleString('pt-BR')+' de '+fil.length.toLocaleString('pt-BR')+'</span><div style="display:flex;gap:4px;flex-wrap:wrap">'+b+'</div>';
}
function ir(p){const pages=Math.ceil(fil.length/PS);if(p<1||p>pages)return;pg=p;render();window.scrollTo({top:0,behavior:'smooth'});}
function kpis(){
  const sm=c=>fil.reduce((a,r)=>a+r[c],0);
  const dif=sm('DIFERENCA_AF_B');
  const cd=fil.filter(r=>Math.abs(r.DIFERENCA_AF_B)>=0.005).length;
  const tot=fil.length;
  const dc=Math.abs(dif)<0.01?'ko':dif>0?'kw':'ka';
  const pct=tot>0?(cd/tot*100).toFixed(1):0;
  document.getElementById('krow').innerHTML=
    '<div class="kpi"><div class="kl">Ativo Financeiro (AF)</div><div class="kv '+vc(sm('AF'))+'">'+brl(sm('AF'))+'</div><div class="ks">Contas 1XXXXXXXX (F)</div></div>'
   +'<div class="kpi"><div class="kl">Passivo Financeiro (PF)</div><div class="kv '+vc(sm('PF'))+'">'+brl(sm('PF'))+'</div><div class="ks">Contas 22XXXXXXX (F)</div></div>'
   +'<div class="kpi"><div class="kl">RPNP</div><div class="kv '+vc(sm('RPNP'))+'">'+brl(sm('RPNP'))+'</div><div class="ks">Conta 631100000</div></div>'
   +'<div class="kpi"><div class="kl">AF − (PF + RPNP) \xb7 coluna (a)</div><div class="kv '+vc(sm('AF_MENOS_PF_RPNP'))+'">'+brl(sm('AF_MENOS_PF_RPNP'))+'</div><div class="ks">Antes da conta 721190300</div></div>'
   +'<div class="kpi"><div class="kl">Disponibilidades \xb7 Coluna (b)</div><div class="kv '+vc(sm('CONTA_721190300'))+'">'+brl(sm('CONTA_721190300'))+'</div><div class="ks">Conta 721190300</div></div>'
   +'<div class="kpi '+dc+'"><div class="kl">Diferen\xe7a (a − b)</div><div class="kv '+vc(dif)+'">'+brl(dif)+'</div>'
   +'<div class="ks"><span class="badge '+(cd>0?'br':'bg')+'">'+cd.toLocaleString('pt-BR')+' doc'+(cd!==1?'s':'')+' c/ dif. \xb7 '+pct+'%</span></div></div>';
}
let evData=[];
function renderEventos(){
  const evMap={};
  fil.filter(r=>Math.abs(r.DIFERENCA_AF_B)>=0.005&&r.COEVENTO!=='—').forEach(r=>{
    const ev=String(r.COEVENTO).trim();if(!ev||ev==='—')return;
    if(!evMap[ev])evMap[ev]={n:0,dif:0};
    evMap[ev].n++;evMap[ev].dif+=r.DIFERENCA_AF_B;
  });
  evData=Object.entries(evMap).sort((a,b)=>b[1].n-a[1].n);
  const n=evData.length;
  document.getElementById('ev-cnt').textContent=n;
  document.getElementById('ev-popup-sub').textContent=n+' evento'+(n!==1?'s':'');
  const etb=document.getElementById('ev-tbody');
  if(!n){etb.innerHTML='<tr><td colspan="3" style="text-align:center;color:var(--muted);padding:16px">Nenhum evento nos dados filtrados.</td></tr>';return;}
  etb.innerHTML=evData.map(function(e){return'<tr><td>'+e[0]+'</td><td>'+e[1].n.toLocaleString('pt-BR')+'</td><td class="'+vc(e[1].dif)+'">'+brl(e[1].dif)+'</td></tr>';}).join('');
}
function toggleEv(e){e.stopPropagation();document.getElementById('ev-popup').classList.toggle('open');}
document.addEventListener('click',function(){var p=document.getElementById('ev-popup');if(p)p.classList.remove('open');});
function exportarEventos(){
  if(!evData.length)return alert('Nenhum evento para exportar.');
  const linhas=['Evento;Numero de Documentos;Diferenca Total'].concat(evData.map(function(e){return e[0]+';'+e[1].n+';'+String(e[1].dif).replace('.',',');}));
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),download:'eventos_causadores.csv'});
  a.click();URL.revokeObjectURL(a.href);
}
function exportar(){
  if(!fil.length)return alert('Nenhum dado para exportar.');
  const cols=['GESTAO','UNIDADE_GESTORA','COGESTAO_EMIT','COUG_EMIT','INMES','DALANCAMENTO','NUDOCUMENTO','COEVENTO','AF','PF','RPNP','CONTA_721190300','AF_MENOS_PF_RPNP','DIFERENCA_AF_B'];
  const linhas=[cols.join(';')].concat(fil.map(r=>cols.map(c=>typeof r[c]==='number'?String(r[c]).replace('.',','):r[c]).join(';')));
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),download:'disponibilidades_lancamento.csv'});
  a.click();URL.revokeObjectURL(a.href);
}
['fg','fm','fs'].forEach(function(id){document.getElementById(id).addEventListener('change',aplicar);});
(async()=>{
  async function decomp(b64){
    const bin=atob(b64),buf=new Uint8Array(bin.length);
    for(let i=0;i<bin.length;i++)buf[i]=bin.charCodeAt(i);
    const ds=new DecompressionStream('gzip');
    const w=ds.writable.getWriter();w.write(buf);w.close();
    const chunks=[];for await(const c of ds.readable)chunks.push(c);
    const out=new Uint8Array(chunks.reduce((a,c)=>a+c.length,0));
    let off=0;for(const c of chunks){out.set(c,off);off+=c.length;}
    return JSON.parse(new TextDecoder().decode(out));
  }
  try{
    const [allData,ugsData]=await Promise.all([decomp(DADOS_B64),decomp(UGS_B64)]);
    ALL=allData;
    Object.assign(window,{UGS:ugsData});
  }catch(e){
    console.error('Erro ao descomprimir dados:',e);
    document.getElementById('ldg').innerHTML='<div style="font-size:16px;color:#ff8f82">Erro ao carregar dados.<br>Recarregue a página.</div>';
    return;
  }
  document.getElementById('ldg').style.display='none';
  init();
})();
</script>
</body>
</html>"""


# ── Oracle ─────────────────────────────────────────────────────────────────────
oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")

def extrair_ugs() -> dict:
    """Retorna dicionário {COUG: NOUG} com nomes de todas as Unidades Gestoras."""
    sql = f"SELECT COUG, NOUG FROM {SCHEMA}UNIDADEGESTORA ORDER BY COUG"
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return {str(row[0]): str(row[1]) for row in cur.fetchall()}

def extrair(ug: str | None) -> pd.DataFrame:
    filtro_ug = "WHERE l.COUGCONTAB = :ug" if ug else ""
    sql = SQL.format(schema=SCHEMA, filtro_ug=filtro_ug)
    params = {"ug": ug} if ug else {}

    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle…")
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        print(f"[{datetime.now():%H:%M:%S}] Executando consulta…")
        with conn.cursor() as cur:
            cur.execute(sql, params)
            colunas = [c[0] for c in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=colunas)

    num_cols = ["AF", "PF", "RPNP", "CONTA_721190300", "AF_MENOS_PF_RPNP", "DIFERENCA_AF_B"]
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    print(f"[{datetime.now():%H:%M:%S}] {len(df):,} lançamentos retornados.")
    return df


# ── Geração do HTML ────────────────────────────────────────────────────────────
def gerar_html(df: pd.DataFrame, ugs: dict) -> str:
    registros = df.to_dict(orient="records")
    for r in registros:
        for k, v in r.items():
            try:
                r[k] = float(v) if hasattr(v, "__float__") else str(v)
            except Exception:
                r[k] = str(v)

    def compress(obj):
        return base64.b64encode(
            gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), compresslevel=9)
        ).decode()

    html = HTML_TEMPLATE
    html = html.replace('{dados}', compress(registros))
    html = html.replace('{ugs}', compress(ugs))
    html = html.replace('{timestamp}', datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    return html


# ── GitHub Pages ───────────────────────────────────────────────────────────────
def publicar_github(caminho: str, mensagem_commit: str) -> None:
    import subprocess

    pasta = Path(__file__).parent
    url_remote = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"

    def git(*args):
        resultado = subprocess.run(
            ["git", "-C", str(pasta)] + list(args),
            capture_output=True, text=True
        )
        if resultado.returncode != 0:
            raise RuntimeError(resultado.stderr.strip())
        return resultado.stdout.strip()

    print(f"[{datetime.now():%H:%M:%S}] Publicando no GitHub via git…")

    # Garante que o remote usa o token atualizado
    try:
        git("remote", "set-url", "origin", url_remote)
    except RuntimeError:
        git("remote", "add", "origin", url_remote)

    git("add", caminho)
    git("commit", "-m", mensagem_commit)
    git("push", "origin", GITHUB_BRANCH)

    print(f"[{datetime.now():%H:%M:%S}] Publicado com sucesso.")
    print(f"  -> https://{GITHUB_USER}.github.io/{GITHUB_REPO}/{caminho}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Disponibilidades por Lançamento — extrai do Oracle e publica no GitHub Pages.")
    parser.add_argument("--ug",      type=str,            default=None,  help="Filtrar por Unidade Gestora (opcional)")
    parser.add_argument("--out",     type=str,            default=ARQUIVO_HTML, help="Arquivo HTML local de saída")
    parser.add_argument("--no-push", action="store_true", default=False, help="Gera o HTML localmente sem publicar no GitHub")
    args = parser.parse_args()

    # 1. Extrai do Oracle
    try:
        df = extrair(args.ug)
        print(f"[{datetime.now():%H:%M:%S}] Buscando nomes das Unidades Gestoras…")
        ugs = extrair_ugs()
        print(f"[{datetime.now():%H:%M:%S}] {len(ugs):,} UGs carregadas.")
    except oracledb.DatabaseError as e:
        print(f"\nErro de banco de dados: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Gera HTML
    html = gerar_html(df, ugs)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"[{datetime.now():%H:%M:%S}] HTML salvo localmente: {args.out}")

    # 3. Publica no GitHub Pages
    if not args.no_push:
        ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        publicar_github(
            caminho=ARQUIVO_HTML,
            mensagem_commit=f"chore: atualiza disponibilidades por lançamento — {ts}",
        )

    # 4. Resumo
    def brl(v: float) -> str:
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    print("\n-- Resumo -------------------------------------------------------------")
    print(f"  AF total              : {brl(float(df['AF'].sum()))}")
    print(f"  PF total              : {brl(float(df['PF'].sum()))}")
    print(f"  RPNP total            : {brl(float(df['RPNP'].sum()))}")
    print(f"  Diferença (a-b) total : {brl(float(df['DIFERENCA_AF_B'].sum()))}")
    com_dif = int((df["DIFERENCA_AF_B"].abs() > 0.005).sum())
    print(f"  Lançamentos c/ dif.   : {com_dif:,} de {len(df):,}")
    print("-----------------------------------------------------------------------\n")


if __name__ == "__main__":
    main()
