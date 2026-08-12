"""
LANÇAMENTOS INTRAGOVERNAMENTAIS — documentos que abrem as equações A e B (subtítulo 2)
Gera HTML autocontido para análise interativa e publica no GitHub Pages.

Equação A: Classes 1+3 (devedoras) = Classes 2+4 (credoras) — subtítulo 2
Equação B: Classe 3 (VPD) = Classe 4 (VPA) — subtítulo 2

Para cada documento com divergência em A ou B, exibe todas as contas contábeis
(classes 1–4) sensibilizadas, indicando quais linhas contribuem para cada equação.

Uso:
    python extrair_intra_lancamento.py
    python extrair_intra_lancamento.py --mes 7 --ano 2026
    python extrair_intra_lancamento.py --no-push
"""

import argparse, base64, gzip, json, os, subprocess
from datetime import date, datetime
from pathlib import Path

import oracledb
import pandas as pd
from dotenv import load_dotenv

PASTA = Path(__file__).parent
load_dotenv(PASTA / ".env")

ORACLE_USER = os.environ["ORACLE_USER"]
ORACLE_PASS = os.environ["ORACLE_PASS"]
ORACLE_DSN  = os.environ["ORACLE_DSN"]
SCHEMA      = "MIL2026."

INSTANT_CLIENT_DIR = r"C:\oracle\instantclient_23_0"

GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
GITHUB_USER   = os.environ["GITHUB_USER"]
GITHUB_REPO   = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = os.environ["GITHUB_BRANCH"]
ARQUIVO_HTML  = "intra_lancamento.html"

MESES = {1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
         7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}

# ─── SQL ──────────────────────────────────────────────────────────────────────
# Chave do documento = (NUDOCUMENTO, COUG_EMIT, COGESTAO_EMIT).
# docs_div: agrupado por (NUDOCUMENTO, COUG, COGESTAO) — sem INMES — para
#   capturar todas as "pernas" do lançamento mesmo que em meses diferentes.
# JOIN: mesmos 3 campos, garantindo que D e C do mesmo emissor apareçam juntos.
SQL = """
WITH docs_div AS (
  SELECT TRIM(v.NUDOCUMENTO) AS NUDOCUMENTO,
         v.COUG, v.COGESTAO
  FROM {schema}VLANCAMENTOCONTABIL v
  WHERE v.INMES <= :mes
    AND SUBSTR(TO_CHAR(v.COCONTACONTABIL),1,1) IN ('1','2','3','4')
  GROUP BY TRIM(v.NUDOCUMENTO), v.COUG, v.COGESTAO
  HAVING
    ABS(ROUND(SUM(
      CASE WHEN SUBSTR(TO_CHAR(v.COCONTACONTABIL),5,1)='2'
                AND SUBSTR(TO_CHAR(v.COCONTACONTABIL),1,1) IN ('1','2','3','4')
           THEN CASE WHEN v.INDEBITOCREDITO='D' THEN  v.VALANCAMENTO
                     WHEN v.INDEBITOCREDITO='C' THEN -v.VALANCAMENTO ELSE 0 END
           ELSE 0 END), 2)) > 0.005
  OR
    ABS(ROUND(SUM(
      CASE WHEN SUBSTR(TO_CHAR(v.COCONTACONTABIL),5,1)='2'
                AND SUBSTR(TO_CHAR(v.COCONTACONTABIL),1,1) IN ('3','4')
           THEN CASE WHEN v.INDEBITOCREDITO='D' THEN  v.VALANCAMENTO
                     WHEN v.INDEBITOCREDITO='C' THEN -v.VALANCAMENTO ELSE 0 END
           ELSE 0 END), 2)) > 0.005
)
SELECT
  v.INMES,
  v.COGESTAO                                    AS COGESTAO_EMIT,
  v.COUG                                        AS COUG_EMIT,
  ue.NOUG                                       AS NOUG_EMIT,
  ge.NOGESTAO                                   AS NOGESTAO_EMIT,
  v.COGESTAOCONTAB                              AS COGESTAO,
  v.COUGCONTAB                                  AS COUG,
  uc.NOUG                                       AS NOUG,
  gc.NOGESTAO                                   AS NOGESTAO,
  TRIM(v.NUDOCUMENTO)                           AS NUDOCUMENTO,
  TO_CHAR(v.COCONTACONTABIL)                    AS COCONTACONTABIL,
  c.NOCONTACONTABIL,
  SUBSTR(TO_CHAR(v.COCONTACONTABIL),1,1)        AS CLASSE,
  SUBSTR(TO_CHAR(v.COCONTACONTABIL),5,1)        AS SUBTITULO,
  v.COEVENTO,
  TO_CHAR(MIN(v.DALANCAMENTO),'DD/MM/YYYY')     AS DATA,
  TO_CHAR(MIN(v.DALANCAMENTO),'YYYY/MM/DD')     AS DATA_ISO,
  ROUND(SUM(CASE WHEN v.INDEBITOCREDITO='D' THEN  v.VALANCAMENTO
                 WHEN v.INDEBITOCREDITO='C' THEN -v.VALANCAMENTO
                 ELSE 0 END), 2)                AS VLNET
FROM {schema}VLANCAMENTOCONTABIL v
JOIN docs_div d
  ON  TRIM(v.NUDOCUMENTO) = d.NUDOCUMENTO
  AND v.COUG              = d.COUG
  AND v.COGESTAO          = d.COGESTAO
LEFT JOIN {schema}VUNIDADEGESTORA uc ON uc.COUG = v.COUGCONTAB
LEFT JOIN {schema}VUNIDADEGESTORA ue ON ue.COUG = v.COUG
LEFT JOIN {schema}VCONTACONTABIL  c  ON c.COCONTACONTABIL = v.COCONTACONTABIL
LEFT JOIN {schema}GESTAO          gc ON gc.COGESTAO = v.COGESTAOCONTAB
LEFT JOIN {schema}GESTAO          ge ON ge.COGESTAO = v.COGESTAO
WHERE v.INMES <= :mes
  AND SUBSTR(TO_CHAR(v.COCONTACONTABIL),1,1) IN ('1','2','3','4')
GROUP BY
  v.INMES, v.COGESTAO, v.COUG, ue.NOUG, ge.NOGESTAO,
  v.COGESTAOCONTAB, v.COUGCONTAB, uc.NOUG, gc.NOGESTAO,
  TRIM(v.NUDOCUMENTO), TO_CHAR(v.COCONTACONTABIL), c.NOCONTACONTABIL,
  SUBSTR(TO_CHAR(v.COCONTACONTABIL),1,1), SUBSTR(TO_CHAR(v.COCONTACONTABIL),5,1),
  v.COEVENTO
ORDER BY MIN(v.DALANCAMENTO), v.COUG, TRIM(v.NUDOCUMENTO), TO_CHAR(v.COCONTACONTABIL)
"""

# ─── Template HTML ────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<!-- v-intra-lancamento-5 -->
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lançamentos Intragovernamentais — SIGGO</title>
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
/* ── Filtros ── */
.fbar{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}
.fg{display:flex;flex-direction:column;gap:4px}
.fg label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.fg select{border:1.5px solid var(--border);border-radius:6px;padding:7px 28px 7px 10px;font-size:12.5px;min-width:140px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E") no-repeat right 9px center;color:var(--text);cursor:pointer;appearance:none}
.fg select:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-wrap{position:relative;min-width:200px}
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
.btn-eqA{background:#fde8e6;color:var(--red);border:1.5px solid #f5c0ba}
.btn-eqA.active,.btn-eqA:hover{background:var(--red);color:#fff;border-color:var(--red)}
.btn-eqB{background:#fef9e2;color:var(--amber);border:1.5px solid #f0dc8a}
.btn-eqB.active,.btn-eqB:hover{background:#c9950a;color:#fff;border-color:#c9950a}
/* Emitente inline (ao lado da busca) */
.emit-wrap{position:relative;min-width:180px}
.emit-in{border:2px solid var(--teal);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;box-shadow:0 0 0 2px rgba(0,144,168,.08)}
.emit-in:focus{outline:none;border-color:var(--navy);box-shadow:0 0 0 3px rgba(0,144,168,.18)}
.emit-clr{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}
.emit-dd{position:absolute;top:calc(100% + 4px);left:0;min-width:200px;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:300;max-height:220px;overflow-y:auto;display:none}
.emit-dd .ac-dd-item{white-space:nowrap}
/* ── KPIs ── */
.krow{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;padding:18px 28px 4px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}
.kpi.ka::before{background:linear-gradient(90deg,var(--red),#e74c3c)}
.kpi.kb::before{background:linear-gradient(90deg,var(--amber),#e6b800)}
.kpi.ko::before{background:linear-gradient(90deg,var(--green),#27ae60)}
.kl{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.kv{font-size:20px;font-weight:700;letter-spacing:-.3px;line-height:1}
.ks{font-size:11px;color:var(--muted);margin-top:5px}
/* ── Tabela ── */
.tsec{padding:16px 28px 32px}
.thead-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.ttitle{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.tw{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:1001px;table-layout:fixed}
.sw{position:relative}
.sw input{border:2px solid var(--teal);border-radius:6px;padding:7px 12px 7px 32px;font-size:12.5px;min-width:280px;background:#fff;box-shadow:0 0 0 2px rgba(0,144,168,.08)}
.sw input:focus{outline:none;border-color:var(--navy);box-shadow:0 0 0 3px rgba(0,144,168,.18)}
.sw::before{content:'🔍';position:absolute;left:9px;top:50%;transform:translateY(-50%);font-size:12px;pointer-events:none}
thead th{background:var(--navy);color:#c8d8ec;padding:10px 12px;font-size:11px;font-weight:600;text-align:right;white-space:nowrap;letter-spacing:.3px;cursor:pointer;user-select:none;overflow:hidden;text-overflow:ellipsis}
thead th.nosort{cursor:default}
thead th.left{text-align:left}
thead th:hover:not(.nosort){background:#2a4a7a}
.si{display:inline-block;width:13px;text-align:center;opacity:.6;font-size:10px}
/* Linha de documento (dropdown header) */
tr.row-doc{background:#1e3267;cursor:pointer}
tr.row-doc td{color:#e8f0fc;font-weight:700;padding:9px 12px;font-size:12px;white-space:nowrap;text-align:right}
tr.row-doc td.left{text-align:left}
tr.row-doc:hover td{background:#2a4580}
/* Linhas de detalhe (contas) */
tr.dr{background:var(--surface);display:none}
tr.dr.alt{background:var(--row-alt)}
tr.dr td{padding:7px 12px;border-bottom:1px solid var(--border);font-size:12px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;overflow:hidden;text-overflow:ellipsis;padding-left:24px}
tr.dr td.left{text-align:left}
tr.dr td code{font-family:'Consolas','Courier New',monospace;font-size:11px;font-weight:700;color:var(--navy)}
tr.dr:hover td{background:var(--hover)}
/* Rodapé */
tfoot tr td{background:#e8f0f8;font-weight:700;border-top:2px solid var(--teal);padding:10px 12px;font-size:12px;text-align:right}
tfoot tr td.left{text-align:left}
.tog{display:inline-block;width:14px;text-align:center;font-style:normal;margin-right:4px}
.vp{color:var(--green);font-weight:600}
.vn{color:var(--red);font-weight:600}
.vz{color:var(--muted)}
.badge{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase}
.br{background:#fde8e6;color:var(--red)}.bg{background:#e6f5ec;color:var(--green)}.bz{background:#eef1f6;color:var(--muted)}
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
    <div class="hlogo">2️⃣</div>
    <h1>Lançamentos Intragovernamentais
      <span>SIGGO · Ano Exercício 2026</span>
    </h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <span id="ts">Atualizado em {timestamp}</span>
</header>

<div class="fbar">
  <!-- Gestão (contabilizadora) -->
  <div class="fg">
    <label>Gestão</label>
    <div class="ac-wrap" style="min-width:130px">
      <input id="fg-input" class="ac-input" type="text" placeholder="Código…" autocomplete="off"
             oninput="onAC('g')" onfocus="onAC('g')" onblur="blurAC('g')">
      <button class="ac-clear" id="fg-clear" onclick="limparAC('g')">✕</button>
      <div class="ac-dd" id="fg-dd"></div>
    </div>
  </div>
  <!-- Unidade Gestora (contabilizadora) -->
  <div class="fg">
    <label>Unidade Gestora</label>
    <div class="ac-wrap" style="min-width:230px">
      <input id="fu-input" class="ac-input" type="text" placeholder="Código ou nome…" autocomplete="off"
             oninput="onAC('u')" onfocus="onAC('u')" onblur="blurAC('u')">
      <button class="ac-clear" id="fu-clear" onclick="limparAC('u')">✕</button>
      <div class="ac-dd" id="fu-dd"></div>
    </div>
  </div>
  <!-- Mês -->
  <div class="fg">
    <label>Mês</label>
    <select id="fm"><option value="">Todos</option></select>
  </div>
  <!-- Conta Contábil -->
  <div class="fg">
    <label>Conta Contábil</label>
    <div class="ac-wrap" style="min-width:230px">
      <input id="fc-input" class="ac-input" type="text" placeholder="Código ou nome…" autocomplete="off"
             oninput="onAC('c')" onfocus="onAC('c')" onblur="blurAC('c')">
      <button class="ac-clear" id="fc-clear" onclick="limparAC('c')">✕</button>
      <div class="ac-dd" id="fc-dd"></div>
    </div>
  </div>
  <!-- Grupo de Contas -->
  <div class="bgrp">
    <button class="btn btn-eqA" id="btn-eqA" onclick="filtrarEq('eqA')">Divergências 1+3 = 2+4</button>
    <button class="btn btn-eqB" id="btn-eqB" onclick="filtrarEq('eqB')">Divergências 3 = 4</button>
    <button class="btn btn-g" onclick="limpar()">↺ Limpar filtros</button>
    <button class="btn btn-p" onclick="exportarCSV()">↓ Exportar CSV</button>
  </div>
</div>

<div class="krow">
  <div class="kpi" id="kpi-a">
    <div class="kl">Divergências — Equação A</div>
    <div class="kv" id="kv-a">—</div>
    <div class="ks">Classes 1+3 = Classes 2+4 (somente subtítulo 2)</div>
  </div>
  <div class="kpi" id="kpi-b">
    <div class="kl">Divergências — Equação B</div>
    <div class="kv" id="kv-b">—</div>
    <div class="ks">Classe 3 = Classe 4 (somente subtítulo 2)</div>
  </div>
</div>

<div class="tsec">
  <div class="thead-row">
    <span class="ttitle" id="ttitle">Documentos</span>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <div class="emit-wrap">
        <input id="emit-in" class="emit-in" type="text" placeholder="Gestão-UG Emitente…"
               autocomplete="off" oninput="onEmit()" onfocus="onEmit()" onblur="blurEmit()">
        <button class="emit-clr" id="emit-clr" onclick="limparEmit()">✕</button>
        <div class="emit-dd" id="emit-dd"></div>
      </div>
      <div class="sw"><input id="busca" type="text" placeholder="Buscar por nº documento ou evento…"></div>
      <div id="pgbar-top" class="pgbar"></div>
    </div>
  </div>
  <div class="tw">
    <table>
      <thead>
        <tr>
          <th class="left nosort" style="width:36px"></th>
          <th class="left" style="width:220px" onclick="sortBy('UG')">Gestão · UG<span id="s_UG" class="si">⇅</span></th>
          <th class="left nosort" style="width:115px">Conta Contábil</th>
          <th style="width:110px" onclick="sortBy('DIVA')">Div. Eq. A<span id="s_DIVA" class="si">⇅</span></th>
          <th style="width:110px" onclick="sortBy('DIVB')">Div. Eq. B<span id="s_DIVB" class="si">⇅</span></th>
          <th style="width:90px" onclick="sortBy('DATA')">Data Lanç.<span id="s_DATA" class="si">⇅</span></th>
          <th class="left nosort" style="width:120px">Gestão-UG Emitente</th>
          <th class="left" style="width:130px" onclick="sortBy('NUDOC')">Nº Documento<span id="s_NUDOC" class="si">⇅</span></th>
          <th class="left nosort" style="width:70px">Evento</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
      <tfoot><tr id="tfoot-row"><td class="left" colspan="9">—</td></tr></tfoot>
    </table>
  </div>
  <div id="pgbar-bot" class="pgbar"></div>
</div>

<script>
(function(){
const NOMES_MES={1:'Janeiro',2:'Fevereiro',3:'Março',4:'Abril',5:'Maio',6:'Junho',
                 7:'Julho',8:'Agosto',9:'Setembro',10:'Outubro',11:'Novembro',12:'Dezembro'};
const PG_SZ=50;
let fil=[], filDocs=[], pg=1, sortCol='DATA', sortDir=1, filterMode='all';
let emitSel='';

/* ── Descompressão ── */
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
decomp('{dados}').then(txt=>{window.ALL=JSON.parse(txt);init();});

/* ── Helpers ── */
function n(v){return v==null?0:+v||0;}
function brl(v){
  if(v==null||isNaN(v))return'—';
  const s=v<0?'-':'',a=Math.abs(v);
  return s+'R$ '+a.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
}
function vc(v){return Math.abs(n(v))<0.005?'vz':n(v)>0?'vp':'vn';}
/* Gestão: sem prefixo G, 6 dígitos com zeros à esquerda */
const fmtG=v=>v!=null?String(v).padStart(6,'0'):'—';
/* Linha UG contabilizadora: "000001 · 130101 — NOME" */
function ugLabel(g,u,nome){return fmtG(g)+' · '+String(u)+(nome?' — '+nome:'');}
/* Emitente enxuto: "000001-130101" */
function emitLabel(g,u){return fmtG(g)+'-'+String(u);}

/* Equação A: subtítulo 2, classes 1–4 */
function divA(r){return r.SUBTITULO==='2'?n(r.VLNET):0;}
/* Equação B: subtítulo 2, classes 3–4 */
function divB(r){return r.SUBTITULO==='2'&&(r.CLASSE==='3'||r.CLASSE==='4')?n(r.VLNET):0;}

/* ── Autocomplete genérico (gestão contab, UG contab, conta) ── */
const AC={g:{sel:'',label:''},u:{sel:'',label:''},c:{sel:'',label:''}};

function onAC(k){
  const inp=document.getElementById('f'+k+'-input').value.trim().toLowerCase();
  const dd=document.getElementById('f'+k+'-dd');
  let opts=[];
  if(k==='g'){
    /* Gestões contabilizadoras com nome: "000001 - TESOURO" */
    const gm=new Map();
    window.ALL.forEach(r=>{if(r.COGESTAO!=null&&!gm.has(String(r.COGESTAO)))gm.set(String(r.COGESTAO),(r.NOGESTAO||'').trim());});
    const gs=[...gm.entries()].sort((a,b)=>a[0].localeCompare(b[0]));
    opts=gs.filter(([id,nm])=>!inp||fmtG(id).includes(inp)||nm.toLowerCase().includes(inp)).slice(0,40)
           .map(([id,nm])=>{const lbl=(fmtG(id)+(nm?' - '+nm:'')).replace(/'/g,'&#39;');return`<div class="ac-dd-item" onclick="selAC('g','${id}','${lbl}')"><strong>${fmtG(id)}</strong>${nm?' - '+nm:''}</div>`;});
  } else if(k==='u'){
    /* UGs contabilizadoras (COUGCONTAB / COUG do SELECT) */
    const us=[...new Map(window.ALL.map(r=>[String(r.COUG),(r.NOUG||'').trim()]))].sort((a,b)=>a[0].localeCompare(b[0]));
    opts=us.filter(([id,nm])=>!inp||id.includes(inp)||nm.toLowerCase().includes(inp)).slice(0,40)
           .map(([id,nm])=>`<div class="ac-dd-item" onclick="selAC('u','${id}','${id}${nm?' — '+nm:''}')"><strong>${id}</strong>${nm?' — '+nm:''}</div>`);
  } else {
    const cs=[...new Map(window.ALL.map(r=>[r.COCONTACONTABIL,(r.NOCONTACONTABIL||'').trim()]))].sort((a,b)=>a[0].localeCompare(b[0]));
    opts=cs.filter(([id,nm])=>!inp||id.includes(inp)||nm.toLowerCase().includes(inp)).slice(0,40)
           .map(([id,nm])=>`<div class="ac-dd-item" onclick="selAC('c','${id}','${id}${nm?' — '+nm:''}')"><strong>${id}</strong>${nm?' — '+nm:''}</div>`);
  }
  dd.innerHTML=opts.length?opts.join(''):'<div class="ac-dd-empty">Nenhum resultado</div>';
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

/* ── Autocomplete Gestão-UG Emitente (inline, ao lado da busca) ── */
let emitList=[];
function buildEmitList(){
  emitList=[...new Set(window.ALL.map(r=>emitLabel(r.COGESTAO_EMIT,r.COUG_EMIT)))].sort();
}
function onEmit(){
  const v=document.getElementById('emit-in').value.trim().toLowerCase();
  const m=v?emitList.filter(e=>e.toLowerCase().includes(v)):emitList;
  const dd=document.getElementById('emit-dd');
  dd.innerHTML=m.length?m.slice(0,60).map(e=>`<div class="ac-dd-item" onmousedown="selEmit('${e}')">${e}</div>`).join(''):'<div class="ac-dd-empty">Nenhum resultado</div>';
  dd.style.display='block';
}
function selEmit(v){
  emitSel=v;
  document.getElementById('emit-in').value=v;
  document.getElementById('emit-clr').style.display='block';
  document.getElementById('emit-dd').style.display='none';
  aplicar();
}
function limparEmit(){
  emitSel='';
  document.getElementById('emit-in').value='';
  document.getElementById('emit-clr').style.display='none';
  document.getElementById('emit-dd').style.display='none';
  aplicar();
}
function blurEmit(){setTimeout(()=>document.getElementById('emit-dd').style.display='none',150);}

/* ── Botões de equação ── */
function filtrarEq(mode){
  filterMode=(filterMode===mode)?'all':mode;
  document.getElementById('btn-eqA').classList.toggle('active',filterMode==='eqA');
  document.getElementById('btn-eqB').classList.toggle('active',filterMode==='eqB');
  aplicar();
}

/* ── Ordenação ── */
function sortBy(col){
  if(sortCol===col){sortDir*=-1;}else{sortCol=col;sortDir=1;}
  ['UG','DIVA','DIVB','DATA','NUDOC'].forEach(c=>{
    const el=document.getElementById('s_'+c);
    if(el)el.textContent=c===sortCol?(sortDir>0?'↑':'↓'):'⇅';
  });
  filDocs.sort(cmpDocs);
  pg=1;render();
}
function cmpDocs(a,b){
  if(sortCol==='DATA')   return sortDir*(a.minDataISO||'').localeCompare(b.minDataISO||'');
  if(sortCol==='NUDOC')  return sortDir*String(a.NUDOCUMENTO).localeCompare(String(b.NUDOCUMENTO),'pt-BR');
  if(sortCol==='DIVA')   return sortDir*(n(a.totDivA)-n(b.totDivA));
  if(sortCol==='DIVB')   return sortDir*(n(a.totDivB)-n(b.totDivB));
  if(sortCol==='UG')     return sortDir*(a.emitKey||'').localeCompare(b.emitKey||'','pt-BR');
  return 0;
}

/* ── Init ── */
function init(){
  buildEmitList();
  const meses=[...new Set(window.ALL.map(r=>r.INMES))].sort((a,b)=>a-b);
  const fm=document.getElementById('fm');
  meses.forEach(m=>{const o=document.createElement('option');o.value=m;o.textContent=NOMES_MES[m]||m;fm.appendChild(o);});
  fm.addEventListener('change',aplicar);
  document.getElementById('busca').addEventListener('input',aplicar);
  document.getElementById('emit-in').addEventListener('blur',blurEmit);
  /* Ícone inicial de ordenação (DATA crescente) */
  const el=document.getElementById('s_DATA');
  if(el)el.textContent='↑';
  aplicar();
}

/* ── Filtros → docs ── */
function aplicar(){
  const fmes=document.getElementById('fm').value;
  const busca=(document.getElementById('busca').value||'').trim().toLowerCase();

  /* Filtro de linhas individuais */
  fil=window.ALL.filter(r=>{
    if(AC.g.sel&&String(r.COGESTAO)!==AC.g.sel)return false;
    if(AC.u.sel&&String(r.COUG)!==AC.u.sel)return false;
    if(fmes&&String(r.INMES)!==fmes)return false;
    if(AC.c.sel&&r.COCONTACONTABIL!==AC.c.sel)return false;
    return true;
  });

  /* Agrupa por chave: Gestão-UG Emitente + NUDOCUMENTO (sem INMES — doc pode ter lançamentos em meses diferentes) */
  const map={};
  fil.forEach(r=>{
    const ek=emitLabel(r.COGESTAO_EMIT,r.COUG_EMIT);
    const k=ek+'|'+(r.NUDOCUMENTO||'');
    if(!map[k])map[k]={key:k,emitKey:ek,NUDOCUMENTO:r.NUDOCUMENTO,
                       rows:[],minData:null,minDataISO:null,
                       totDivA:0,totDivB:0};
    const d=map[k];
    d.rows.push(r);
    d.totDivA=Math.round((d.totDivA+divA(r))*100)/100;
    d.totDivB=Math.round((d.totDivB+divB(r))*100)/100;
    if(r.DATA_ISO&&(!d.minDataISO||r.DATA_ISO<d.minDataISO)){d.minDataISO=r.DATA_ISO;d.minData=r.DATA;}
  });
  /* Aplica filtros de emitente, equação e busca */
  filDocs=Object.values(map).filter(d=>{
    if(Math.abs(d.totDivA)<0.005&&Math.abs(d.totDivB)<0.005)return false;
    if(emitSel&&d.emitKey!==emitSel)return false;
    if(filterMode==='eqA'&&Math.abs(d.totDivA)<0.005)return false;
    if(filterMode==='eqB'&&Math.abs(d.totDivB)<0.005)return false;
    if(busca){
      const docOk=String(d.NUDOCUMENTO||'').toLowerCase().includes(busca);
      const evOk=d.rows.some(r=>r.COEVENTO!=null&&String(r.COEVENTO).toLowerCase().includes(busca));
      if(!docOk&&!evOk)return false;
    }
    return true;
  });

  filDocs.sort(cmpDocs);
  pg=1;render();kpis();
}

/* ── Render ── */
function render(){
  const tb=document.getElementById('tbody');
  const tf=document.getElementById('tfoot-row');
  const start=(pg-1)*PG_SZ,end=Math.min(start+PG_SZ,filDocs.length);
  let html='';

  filDocs.slice(start,end).forEach(d=>{
    const did='d'+d.key.replace(/[^a-z0-9]/gi,'_');
    const dA=n(d.totDivA),dB=n(d.totDivB);
    /* Linha de documento: "▶ 2026NS00009 · 000001-200101" */
    html+='<tr class="row-doc" onclick="toggleDoc(\''+did+'\')">'
      +'<td class="left" colspan="3"><span class="tog" id="tog_'+did+'">▶</span>'
      +' <code style="color:#9ab0cc;font-size:12px">'+String(d.NUDOCUMENTO||'')+'</code>'
      +' <span style="opacity:.7">·</span>'
      +' <code style="color:#c8d8ec;font-size:12px">'+d.emitKey+'</code>'
      +' <span style="font-size:10px;font-weight:400;opacity:.5">('+d.rows.length+' lançamento'+(d.rows.length!==1?'s':'')+')</span></td>'
      +'<td class="'+vc(dA)+'">'+brl(dA)+'</td>'
      +'<td class="'+vc(dB)+'">'+brl(dB)+'</td>'
      +'<td style="font-size:11px">'+(d.minData||'—')+'</td>'
      +'<td class="left" colspan="3"></td>'
      +'</tr>';
    /* Sub-linhas ordenadas por data */
    const rows=[...d.rows].sort((a,b)=>(a.DATA_ISO||'').localeCompare(b.DATA_ISO||'')||String(a.COCONTACONTABIL).localeCompare(String(b.COCONTACONTABIL)));
    rows.forEach((r,j)=>{
      const dAl=divA(r),dBl=divB(r);
      const ugC=ugLabel(r.COGESTAO,r.COUG,r.NOUG||'');
      const emL=emitLabel(r.COGESTAO_EMIT,r.COUG_EMIT);
      html+='<tr class="dr'+(j%2?' alt':'')+'" data-doc="'+did+'">'
        +'<td></td>'
        +'<td class="left" title="'+ugC+'">'+ugC+'</td>'
        +'<td class="left"><code style="font-size:11px;color:var(--navy)">'+r.COCONTACONTABIL+'</code></td>'
        +'<td class="'+(Math.abs(dAl)<0.005?'vz':vc(dAl))+'">'+( Math.abs(dAl)<0.005?'—':brl(dAl))+'</td>'
        +'<td class="'+(Math.abs(dBl)<0.005?'vz':vc(dBl))+'">'+( Math.abs(dBl)<0.005?'—':brl(dBl))+'</td>'
        +'<td style="font-size:11px">'+(r.DATA||'—')+'</td>'
        +'<td class="left"><code style="font-size:11px">'+emL+'</code></td>'
        +'<td class="left"><code style="font-size:11px">'+String(r.NUDOCUMENTO||'')+'</code></td>'
        +'<td class="left" style="font-size:11px">'+(r.COEVENTO!=null?String(r.COEVENTO):'—')+'</td>'
        +'</tr>';
    });
  });

  tb.innerHTML=html||'<tr><td colspan="9" style="text-align:center;padding:24px;color:var(--muted)">Nenhum documento encontrado.</td></tr>';

  const totA=filDocs.reduce((s,d)=>s+n(d.totDivA),0);
  const totB=filDocs.reduce((s,d)=>s+n(d.totDivB),0);
  tf.innerHTML='<td class="left" colspan="3">Total · '+filDocs.length.toLocaleString('pt-BR')+' documento(s) com divergência</td>'
    +'<td class="'+vc(totA)+'">'+brl(totA)+'</td>'
    +'<td class="'+vc(totB)+'">'+brl(totB)+'</td>'
    +'<td colspan="4"></td>';

  document.getElementById('ttitle').textContent=filDocs.length.toLocaleString('pt-BR')+' documento(s) com divergência';
  paginar();
}

function toggleDoc(id){
  const tog=document.getElementById('tog_'+id);
  const open=tog&&tog.textContent.trim()==='▼';
  document.querySelectorAll('[data-doc="'+id+'"]').forEach(tr=>tr.style.display=open?'none':'table-row');
  if(tog)tog.textContent=open?'▶':'▼';
}

/* ── KPIs ── */
function kpis(){
  const totA=filDocs.reduce((s,d)=>s+n(d.totDivA),0);
  const totB=filDocs.reduce((s,d)=>s+n(d.totDivB),0);
  const ka=document.getElementById('kpi-a');
  ka.className='kpi'+(Math.abs(totA)>0.005?' ka':'');
  document.getElementById('kv-a').innerHTML='<span class="'+vc(totA)+'">'+brl(totA)+'</span>';
  const kb=document.getElementById('kpi-b');
  kb.className='kpi'+(Math.abs(totB)>0.005?' kb':'');
  document.getElementById('kv-b').innerHTML='<span class="'+vc(totB)+'">'+brl(totB)+'</span>';
}

/* ── Paginação ── */
function paginar(){
  const pages=Math.ceil(filDocs.length/PG_SZ)||1;
  const html=_pgHtml(pages);
  document.getElementById('pgbar-top').innerHTML=html;
  document.getElementById('pgbar-bot').innerHTML=html;
}
function _pgHtml(pages){
  if(pages<=1)return'';
  const s=(pg-1)*PG_SZ+1,e=Math.min(pg*PG_SZ,filDocs.length);
  let b='<button onclick="ir(pg-1)" '+(pg===1?'disabled':'')+'>‹</button>';
  for(let i=1;i<=pages;i++){
    if(i===1||i===pages||Math.abs(i-pg)<=1)
      b+='<button '+(i===pg?'class="pgcur"':'')+' onclick="ir('+i+')">'+i+'</button>';
    else if(Math.abs(i-pg)===2)
      b+='<button disabled style="border:none;background:none;padding:4px 4px">…</button>';
  }
  b+='<button onclick="ir(pg+1)" '+(pg===pages?'disabled':'')+'>›</button>';
  return '<span style="font-size:11px;color:var(--muted)">'+s+'–'+e+' de '+filDocs.length.toLocaleString('pt-BR')+'</span><div style="display:flex;gap:4px;flex-wrap:wrap">'+b+'</div>';
}
function ir(p){const pages=Math.ceil(filDocs.length/PG_SZ);if(p<1||p>pages)return;pg=p;render();window.scrollTo({top:0,behavior:'smooth'});}

/* ── Exportar CSV ── */
function exportarCSV(){
  const linhas=['Mês;Gestão Contab;UG Contab;Nome UG Contab;Gestão Emit;UG Emit;Nome UG Emit;Documento;Conta Contábil;Nome Conta;Classe;Subtítulo;Evento;Data;Vlr Líquido;Div Eq A;Div Eq B'];
  fil.forEach(r=>{
    const dA=divA(r),dB=divB(r);
    linhas.push([r.INMES,fmtG(r.COGESTAO),r.COUG,(r.NOUG||'').trim(),
                 fmtG(r.COGESTAO_EMIT),r.COUG_EMIT,(r.NOUG_EMIT||'').trim(),
                 r.NUDOCUMENTO||'',r.COCONTACONTABIL,(r.NOCONTACONTABIL||'').trim(),
                 r.CLASSE,r.SUBTITULO,r.COEVENTO!=null?r.COEVENTO:'',
                 r.DATA||'',String(n(r.VLNET)).replace('.',','),
                 String(dA).replace('.',','),String(dB).replace('.',',')]
                .join(';'));
  });
  const a=Object.assign(document.createElement('a'),{
    href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),
    download:'intra_lancamento.csv'
  });
  document.body.appendChild(a);a.click();a.remove();
}

/* ── Limpar ── */
function limpar(){
  ['g','u','c'].forEach(k=>limparAC(k));
  limparEmit();
  document.getElementById('fm').value='';
  document.getElementById('busca').value='';
  filterMode='all';
  document.getElementById('btn-eqA').classList.remove('active');
  document.getElementById('btn-eqB').classList.remove('active');
  aplicar();
}

window.onAC=onAC;window.selAC=selAC;window.limparAC=limparAC;window.blurAC=blurAC;
window.onEmit=onEmit;window.selEmit=selEmit;window.limparEmit=limparEmit;
window.filtrarEq=filtrarEq;window.toggleDoc=toggleDoc;window.sortBy=sortBy;
window.aplicar=aplicar;window.limpar=limpar;window.exportarCSV=exportarCSV;window.ir=ir;
})();
</script>
</body>
</html>"""


# ─── Extração ─────────────────────────────────────────────────────────────────
def extrair(mes: int) -> pd.DataFrame:
    oracledb.init_oracle_client(lib_dir=INSTANT_CLIENT_DIR)
    sql = SQL.format(schema=SCHEMA)
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        print(f"  Executando query (INMES 0–{mes})…")
        df = pd.read_sql(sql, conn, params={"mes": mes})
    print(f"  {len(df):,} linhas retornadas.")
    return df


# ─── Geração do HTML ──────────────────────────────────────────────────────────
def gerar_html(df: pd.DataFrame) -> str:
    registros = df.to_dict(orient="records")
    for r in registros:
        for k, v in list(r.items()):
            if isinstance(v, float) and pd.isna(v):
                r[k] = None
            elif hasattr(v, "item"):
                r[k] = v.item()

    json_str  = json.dumps(registros, ensure_ascii=False, separators=(",", ":"))
    dados_b64 = base64.b64encode(
        gzip.compress(json_str.encode("utf-8"), compresslevel=9)
    ).decode()

    print(f"  JSON: {len(json_str)/1024:.0f} KB → comprimido: {len(dados_b64)/1024:.0f} KB")

    ugs = {}
    for r in registros:
        if r.get("COUG") and r.get("NOUG"):
            ugs[str(r["COUG"])] = (r["NOUG"] or "").strip()
        if r.get("COUG_EMIT") and r.get("NOUG_EMIT"):
            ugs[str(r["COUG_EMIT"])] = (r["NOUG_EMIT"] or "").strip()

    html = HTML_TEMPLATE
    html = html.replace("{dados}", dados_b64)
    html = html.replace("{ugs}",   json.dumps(ugs, ensure_ascii=False))
    html = html.replace("{timestamp}", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    return html


# ─── Publicação ───────────────────────────────────────────────────────────────
def publicar(html_path: Path) -> str:
    if os.environ.get("NO_GIT_PUSH"):
        return ""
    pasta = str(html_path.parent)
    msg   = f"chore: atualiza lancamentos intra — {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    subprocess.run(["git", "-C", pasta, "add", html_path.name], check=True)
    subprocess.run(["git", "-C", pasta, "commit", "-m", msg], check=True)
    subprocess.run(["git", "-C", pasta, "pull", "--rebase", "--autostash",
                    "origin", GITHUB_BRANCH], check=True)
    repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
    subprocess.run(["git", "-C", pasta, "push", repo_url, GITHUB_BRANCH], check=True)
    return f"https://{GITHUB_USER}.github.io/{GITHUB_REPO}/{ARQUIVO_HTML}"


# ─── Principal ────────────────────────────────────────────────────────────────
def main():
    hoje    = date.today()
    mes_def = hoje.month

    p = argparse.ArgumentParser()
    p.add_argument("--mes",     type=int, default=mes_def)
    p.add_argument("--ano",     type=int, default=hoje.year)
    p.add_argument("--no-push", action="store_true")
    a = p.parse_args()

    if a.no_push:
        os.environ["NO_GIT_PUSH"] = "1"

    print(f"[{datetime.now():%H:%M:%S}] Extraindo lançamentos intra "
          f"({MESES.get(a.mes, a.mes)}/{a.ano})…")

    df   = extrair(a.mes)
    html = gerar_html(df)

    html_path = PASTA / ARQUIVO_HTML
    html_path.write_text(html, encoding="utf-8")
    print(f"  HTML salvo: {html_path}")

    url = publicar(html_path)
    if url:
        print(f"  Publicado: {url}")
    print("  Concluído.")


if __name__ == "__main__":
    main()
