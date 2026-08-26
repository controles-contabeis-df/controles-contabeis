"""
Repasses e Subrepasses Concedidos/Recebidos — SIGGO/Oracle
Compara repasses recebidos (961210200/300/500) com liquidacoes (622130300/400)
agrupados por UG + Fonte + GND.

Uso:
    python extrair_repasses.py
    python extrair_repasses.py --ug 320203
    python extrair_repasses.py --no-push
"""

import argparse
import base64
import gzip
import json
import math
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
ARQUIVO_HTML  = "repasses.html"

# ── SQL ────────────────────────────────────────────────────────────────────────
# Repasses 961210200/300/500: COCONTACORRENTE 16 dig → pos 7-15 = Fonte, pos 16 = GND
# Repasse  961210600:         COCONTACORRENTE       → pos 1-9  = Fonte, pos 10 = GND
# Liquidacoes 622130300/400:  COCONTACORRENTE 40 dig → pos 24-32 = Fonte, pos 34 = GND
SQL = """
WITH fontes AS (
    SELECT COFONTE, COFONTEFEDERAL
    FROM {schema}FONTERECURSO
),
repasse AS (
    SELECT COGESTAO, COUG, COFONTE, GND,
           ROUND(SUM(VALOR_REPASSE), 2) AS VALOR_REPASSE
    FROM (
        SELECT s.COGESTAO, s.COUG,
               SUBSTR(s.COCONTACORRENTE, 7, 9)  AS COFONTE,
               SUBSTR(s.COCONTACORRENTE, 16, 1) AS GND,
               SUM(s.VACREDITO - s.VADEBITO)    AS VALOR_REPASSE
        FROM {schema}VSALDOCONTABIL s
        WHERE s.COCONTACONTABIL IN (961210200, 961210300, 961210500)
          AND SUBSTR(s.COCONTACORRENTE, 7, 1) NOT IN ('2', '4')
          {filtro_ug}
          {filtro_mes}
        GROUP BY s.COGESTAO, s.COUG,
                 SUBSTR(s.COCONTACORRENTE, 7, 9),
                 SUBSTR(s.COCONTACORRENTE, 16, 1)
        UNION ALL
        SELECT s.COGESTAO, s.COUG,
               SUBSTR(s.COCONTACORRENTE, 1, 9)  AS COFONTE,
               SUBSTR(s.COCONTACORRENTE, 10, 1) AS GND,
               SUM(s.VACREDITO - s.VADEBITO)    AS VALOR_REPASSE
        FROM {schema}VSALDOCONTABIL s
        WHERE s.COCONTACONTABIL = 961210600
          AND SUBSTR(s.COCONTACORRENTE, 1, 1) NOT IN ('2', '4')
          {filtro_ug}
          {filtro_mes}
        GROUP BY s.COGESTAO, s.COUG,
                 SUBSTR(s.COCONTACORRENTE, 1, 9),
                 SUBSTR(s.COCONTACORRENTE, 10, 1)
    )
    GROUP BY COGESTAO, COUG, COFONTE, GND
),
liquidacao AS (
    SELECT
        s.COGESTAO,
        s.COUG,
        SUBSTR(s.COCONTACORRENTE, 24, 9)         AS COFONTE,
        SUBSTR(s.COCONTACORRENTE, 34, 1)         AS GND,
        ROUND(SUM(s.VACREDITO - s.VADEBITO), 2) AS VALOR_LIQ
    FROM {schema}VSALDOCONTABIL s
    WHERE s.COCONTACONTABIL IN (622130300, 622130400)
      AND SUBSTR(s.COCONTACORRENTE, 24, 1) NOT IN ('2', '4')
      {filtro_ug}
      {filtro_mes}
    GROUP BY s.COGESTAO, s.COUG,
             SUBSTR(s.COCONTACORRENTE, 24, 9),
             SUBSTR(s.COCONTACORRENTE, 34, 1)
)
SELECT
    COGESTAO, COUG, FONTE4, GND,
    MAX(COFONTEFEDERAL)                                    AS COFONTEFEDERAL,
    SUM(VALOR_REPASSE)                                     AS VALOR_REPASSE,
    SUM(VALOR_LIQ)                                         AS VALOR_LIQ,
    NVL(SUM(VALOR_REPASSE), 0) - NVL(SUM(VALOR_LIQ), 0)  AS DIFERENCA
FROM (
    SELECT
        COALESCE(r.COGESTAO, l.COGESTAO)                       AS COGESTAO,
        COALESCE(r.COUG,     l.COUG)                           AS COUG,
        SUBSTR(COALESCE(r.COFONTE, l.COFONTE), 1, 4)           AS FONTE4,
        f.COFONTEFEDERAL                                        AS COFONTEFEDERAL,
        COALESCE(r.GND,      l.GND)                            AS GND,
        r.VALOR_REPASSE                                         AS VALOR_REPASSE,
        l.VALOR_LIQ                                             AS VALOR_LIQ
    FROM repasse r
    FULL OUTER JOIN liquidacao l
        ON  r.COGESTAO = l.COGESTAO
        AND r.COUG     = l.COUG
        AND r.COFONTE  = l.COFONTE
        AND r.GND      = l.GND
    LEFT JOIN fontes f
        ON  TO_NUMBER(f.COFONTE) = TO_NUMBER(COALESCE(r.COFONTE, l.COFONTE))
)
GROUP BY COGESTAO, COUG, FONTE4, GND
ORDER BY 1, 2, 3, 4
"""

# ── HTML Template ──────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Repasses e Subrepasses — SIGGO</title>
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
.fg select,.fg input[type=text]{border:1.5px solid var(--border);border-radius:6px;padding:7px 10px;font-size:12.5px;min-width:130px;background:#fff;color:var(--text);cursor:pointer}
.fg select{padding-right:28px;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 9px center;appearance:none}
.fg select:focus,.fg input[type=text]:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ug-wrap{position:relative;min-width:200px}
.ug-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text)}
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
.btn-r{background:#c0392b;color:#fff}
.kpi-row{display:flex;flex-wrap:wrap;gap:14px;padding:18px 28px;background:var(--bg)}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden;flex:1;min-width:160px}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}
.kpi.kw::before{background:linear-gradient(90deg,#f0a500,#ffcc44)}
.kpi.ka::before{background:linear-gradient(90deg,var(--red),#e74c3c)}
.kpi.ko::before{background:linear-gradient(90deg,var(--green),#27ae60)}
.kl{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.kv{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.ks{font-size:10.5px;color:var(--muted);margin-top:4px}
.vp{color:var(--green)}
.vn{color:var(--red)}
.vz{color:var(--muted)}
.badge{display:inline-block;border-radius:10px;padding:1px 8px;font-size:10px;font-weight:700}
.br{background:#fde8e6;color:var(--red)}
.bg{background:#e8f5ee;color:var(--green)}
.cnt-bar{padding:8px 28px;font-size:12px;color:var(--muted);background:var(--surface);border-bottom:1px solid var(--border)}
.tw{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto;margin:18px 28px}
table{width:100%;border-collapse:collapse;table-layout:fixed;min-width:1000px}
thead th{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:right;white-space:nowrap;user-select:none;letter-spacing:.3px}
thead tr:first-child th{text-align:left}
thead .th-sep{background:#162550;color:#7a99bb;font-size:10px;text-transform:uppercase;letter-spacing:.5px;text-align:center;padding:5px 14px;border-bottom:2px solid var(--teal)}
.acct{display:block;font-size:9px;font-weight:400;color:#5a7a9a;letter-spacing:.2px;margin-top:3px;white-space:nowrap}
.kpi .acct{color:var(--muted);font-size:9px;margin-top:2px}
tbody td{padding:9px 14px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;font-size:12.5px;color:var(--text)}
tbody td.rv{color:var(--navy);font-weight:600}
/* Gestão */
tr.row-gestao{background:#1e3267;cursor:pointer}
tr.row-gestao:hover{background:#162550}
tr.row-gestao td{color:#e8f0fc;font-weight:700;padding:10px 14px;font-size:12px;border-bottom:2px solid #0d1b3e;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.row-gestao td:first-child{text-align:left}
tr.row-gestao td.rv{color:#c8dff8}
/* UG */
tr.row-ug{background:#2a4a7f;cursor:pointer}
tr.row-ug:hover{background:#243f6e}
tr.row-ug td{color:#dce8f8;font-weight:700;padding:9px 14px 9px 26px;font-size:12px;border-bottom:1.5px solid #1e3267;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.row-ug td:first-child{text-align:left}
tr.row-ug td.rv{color:#b8d4f5}
/* Fonte+GND */
tr.row-fonte{border-bottom:1px solid var(--border)}
tr.row-fonte:nth-child(even){background:var(--row-alt)}
tr.row-fonte:hover{background:var(--hover)}
tr.row-fonte td:first-child{text-align:left;color:var(--muted);padding-left:42px}
tr.row-fonte td:nth-child(2),tr.row-fonte td:nth-child(3){text-align:left;color:var(--muted)}
.tog{display:inline-block;width:14px;font-size:10px;transition:transform .15s}
tfoot tr td{background:#f0f4fb;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;text-align:right;white-space:nowrap}
tfoot tr td:first-child{text-align:left}
.sep-col{background:rgba(0,144,168,.06);border-left:2px solid var(--teal);border-right:2px solid var(--teal)}
.empty{text-align:center;color:var(--muted);padding:32px;font-size:13px}
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">💵</div>
    <h1>REPASSES E SUBREPASSES
      <span>SIGGO · Ano Exercício 2026</span>
    </h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <div id="ts">Gerado em: {timestamp}</div>
</header>

<div id="ldg" style="display:none;position:fixed;inset:0;background:rgba(13,27,62,.55);z-index:999;align-items:center;justify-content:center">
  <div style="background:#fff;border-radius:12px;padding:28px 36px;font-size:14px;color:#1a2033;box-shadow:0 8px 32px rgba(0,0,0,.25)">Carregando dados…</div>
</div>

<div class="fbar">
  <div class="fg">
    <label>Gestão</label>
    <select id="fgest" onchange="aplicar()"><option value="">Todas</option></select>
  </div>
  <div class="fg ug-wrap" style="min-width:220px">
    <label>Unidade Gestora</label>
    <input class="ug-input" id="ug-inp" placeholder="Código ou nome..." autocomplete="off" oninput="ugInput()" onfocus="ugInput()">
    <button class="ug-clear" id="ug-clr" onclick="limparUG()">✕</button>
    <div class="ug-dd" id="ug-dd"></div>
  </div>
  <div class="fg">
    <label>Ano</label>
    <select id="fano" onchange="trocarAno(this.value)">
      <option value="2026" selected>2026</option>
      <option value="2025">2025</option>
    </select>
  </div>
  <div class="fg">
    <label>Mês de Referência</label>
    <select id="fm" onchange="trocarMes(this.value)"><option value="">Todos</option></select>
  </div>
  <div class="fg">
    <label>Fonte</label>
    <select id="ff" onchange="aplicar()"><option value="">Todas</option></select>
  </div>
  <div class="fg">
    <label>GND</label>
    <select id="fg" onchange="aplicar()"><option value="">Todos</option></select>
  </div>
  <div class="fg">
    <label>Exibir</label>
    <select id="fs" onchange="aplicar()">
      <option value="todos" selected>Todos</option>
      <option value="dif_nz">Com diferença</option>
      <option value="dif_pos">Diferença positiva</option>
      <option value="dif_neg">Diferença negativa</option>
      <option value="sem_rep">Liquidados sem repasse</option>
    </select>
  </div>
  <div class="btns">
    <button class="btn btn-r" onclick="somenteDif()">⚠ Somente Diferenças</button>
    <button class="btn btn-g" onclick="expandirTudo()">▼ Expandir tudo</button>
    <button class="btn btn-g" onclick="recolherTudo()">▲ Recolher tudo</button>
    <button class="btn btn-g" onclick="limpar()">↺ Limpar filtros</button>
    <button class="btn btn-p" onclick="exportar()">↓ Exportar CSV</button>
  </div>
</div>

<div class="kpi-row" id="krow"></div>
<div class="cnt-bar"><span id="cnt"></span></div>

<div class="tw">
  <table>
    <thead>
      <tr>
        <th style="width:80px;text-align:left" rowspan="2">UG</th>
        <th style="width:110px;text-align:left" rowspan="2">Fonte</th>
        <th style="width:55px;text-align:left" rowspan="2">GND</th>
        <th style="width:30px" rowspan="2"></th>
        <th colspan="3" class="th-sep">Mês Selecionado</th>
        <th colspan="3" class="th-sep">Acumulado no Exercício</th>
      </tr>
      <tr>
        <th style="width:150px">Repassado no Mês<span class="acct">961210200 · 300 · 500 · 600</span></th>
        <th style="width:130px">Liq. no Mês<span class="acct">622130300 · 400</span></th>
        <th style="width:120px">Diferença</th>
        <th style="width:150px">Total Repassado<span class="acct">961210200 · 300 · 500 · 600</span></th>
        <th style="width:130px">Total Liquidado<span class="acct">622130300 · 400</span></th>
        <th style="width:120px">Diferença</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
    <tfoot><tr id="tfoot"></tr></tfoot>
  </table>
</div>

<script>
const DADOS_B64={dados_b64};
const TOTAIS_B64={totais_b64};
const UGS_B64='{ugs_b64}';
const MESES=['Saldo Inicial','Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro','Encerramento do Exercício','Encerramento do Exercício'];
const nomeMes=m=>{const n=Number(m);return(n>=0&&n<=14)?n+' · '+MESES[n]:String(m);};
let TOTAL=[],UGS={},CACHE={},mesSel='',anoSel='2026',MES_DATA=[],fil=[],ugSel='';

const brl=v=>{if(v===null||v===undefined||isNaN(v))return'—';const r=Math.round(Number(v)*100)/100;return(r||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});};
const vc=v=>(!v||Math.abs(v)<0.005)?'vz':v>0?'vp':'vn';
const num=v=>(v===null||v===undefined||isNaN(v))?0:Number(v);

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
async function carregarMes(ano,mes){
  const chave=ano+'_'+mes;
  if(CACHE[chave])return CACHE[chave];
  if(!DADOS_B64[chave])return[];
  const data=await decomp(DADOS_B64[chave]);
  CACHE[chave]=data;return data;
}
async function carregarTotal(ano){
  if(CACHE['total_'+ano])return CACHE['total_'+ano];
  const data=await decomp(TOTAIS_B64[ano]);
  CACHE['total_'+ano]=data;return data;
}

(async()=>{
  document.getElementById('ldg').style.display='flex';
  try{
    const[totData,ugsData]=await Promise.all([carregarTotal(anoSel),decomp(UGS_B64)]);
    TOTAL=totData;UGS=ugsData;MES_DATA=[];
  }catch(e){
    document.getElementById('ldg').innerHTML='<div style="background:#fff;border-radius:12px;padding:28px 36px;color:#c0392b">Erro ao carregar dados.</div>';return;
  }
  document.getElementById('ldg').style.display='none';
  initFiltros();
  /* abrir no último mês real (1-12) de 2026 */
  const fmEl=document.getElementById('fm');
  const mesPadrao='{default_mes}';
  if(mesPadrao){
    fmEl.value=mesPadrao;
    await trocarMes(mesPadrao);
  } else {
    aplicar();
  }
})();

async function trocarAno(ano){
  anoSel=ano;mesSel='';MES_DATA=[];TOTAL=[];
  document.getElementById('ldg').style.display='flex';
  try{TOTAL=await carregarTotal(ano);}catch(e){}
  document.getElementById('ldg').style.display='none';
  document.getElementById('fm').value='';
  initFiltrosMes();
  limparUG();
  aplicar();
}
async function trocarMes(mes){
  mesSel=mes;
  document.getElementById('ldg').style.display='flex';
  MES_DATA=await carregarMes(anoSel,mes);
  document.getElementById('ldg').style.display='none';
  aplicar();
}

/* ── Mescla total (acumulado) com mensal por chave UG+Fonte+GND ── */
function mesclar(totalArr, mesArr){
  const key=r=>String(r.COGESTAO)+'|'+String(r.COUG)+'|'+String(r.FONTE4)+'|'+String(r.GND);
  const mesMap={};
  mesArr.forEach(r=>mesMap[key(r)]=r);
  return totalArr.map(r=>{
    const m=mesMap[key(r)]||{};
    return{
      ...r,
      VALOR_REPASSE_MES: m.VALOR_REPASSE??null,
      VALOR_LIQ_MES:     m.VALOR_LIQ??null,
      DIFERENCA_MES:     num(m.DIFERENCA),
    };
  });
}

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
function selUG(k,n){ugSel=k;document.getElementById('ug-inp').value=k+' — '+n;document.getElementById('ug-clr').style.display='block';document.getElementById('ug-dd').style.display='none';aplicar();}
function limparUG(){ugSel='';document.getElementById('ug-inp').value='';document.getElementById('ug-clr').style.display='none';document.getElementById('ug-dd').style.display='none';aplicar();}
document.addEventListener('click',function(e){if(!e.target.closest('.ug-wrap'))document.getElementById('ug-dd').style.display='none';});

/* ── Filtros ── */
function aplicar(){
  const base=mesclar(TOTAL, MES_DATA);
  const fgest=document.getElementById('fgest').value;
  const ff=document.getElementById('ff').value;
  const fg=document.getElementById('fg').value;
  const sd=document.getElementById('fs').value;
  fil=base.filter(r=>{
    if(fgest&&String(r.COGESTAO)!==fgest)return false;
    if(ugSel&&String(r.COUG)!==ugSel)return false;
    if(ff&&String(r.FONTE4)!==ff)return false;
    if(fg&&String(r.GND)!==fg)return false;
    const d=num(r.DIFERENCA);
    if(sd==='dif_pos'&&d<=0)return false;
    if(sd==='dif_neg'&&d>=0)return false;
    if(sd==='dif_nz'&&Math.abs(d)<0.005)return false;
    if(sd==='sem_rep'&&num(r.VALOR_REPASSE)!==0)return false;
    if(sd==='com_rep'&&num(r.VALOR_REPASSE)===0)return false;
    return true;
  });
  render();kpis();
}
function somenteDif(){document.getElementById('fs').value='dif_nz';aplicar();}
function expandirTudo(){
  document.querySelectorAll('.tog').forEach(t=>{
    if(t.dataset.open!=='1'){
      document.querySelectorAll('[data-par="'+t.id.replace('tog_','')+'"]').forEach(tr=>tr.style.display='');
      t.innerHTML='▼';t.dataset.open='1';
    }
  });
}
function recolherTudo(){
  document.querySelectorAll('.tog').forEach(t=>{
    if(t.dataset.open==='1'){t.innerHTML='▶';t.dataset.open='0';}
  });
  document.querySelectorAll('[data-par]').forEach(tr=>tr.style.display='none');
}
function limpar(){document.getElementById('fano').value='2026';document.getElementById('fm').value='';mesSel='';MES_DATA=[];document.getElementById('fgest').value='';document.getElementById('ff').value='';document.getElementById('fg').value='';document.getElementById('fs').value='todos';limparUG();}

function fmtGest(g){return String(g).padStart(5,'0');}
function fmtFonte(r){
  const fed=r.COFONTEFEDERAL?String(r.COFONTEFEDERAL).trim():'';
  const ger=r.FONTE4?String(r.FONTE4).trim():'';
  if(fed&&ger)return fed+'.'+ger;
  return fed||ger||'—';
}

function initFiltrosMes(){
  const prefixo=anoSel+'_';
  const meses=Object.keys(DADOS_B64)
    .filter(k=>k.startsWith(prefixo))
    .map(k=>k.replace(prefixo,''))
    .sort((a,b)=>Number(a)-Number(b));
  const smEl=document.getElementById('fm');
  smEl.innerHTML='<option value="">Todos (Acumulado)</option>';
  meses.forEach(m=>{const o=document.createElement('option');o.value=m;o.textContent=nomeMes(m);smEl.appendChild(o);});
}
function initFiltros(){
  initFiltrosMes();
  const gestoes=[...new Set(TOTAL.map(r=>String(r.COGESTAO)))].sort();
  const sgest=document.getElementById('fgest');
  sgest.innerHTML='<option value="">Todas</option>';
  gestoes.forEach(g=>{const o=document.createElement('option');o.value=g;o.textContent=fmtGest(g);sgest.appendChild(o);});

  const fontes=[...new Set(TOTAL.map(r=>String(r.FONTE4||'')))].filter(Boolean).sort();
  const sf=document.getElementById('ff');
  sf.innerHTML='<option value="">Todas</option>';
  fontes.forEach(f=>{
    const rec=TOTAL.find(r=>String(r.FONTE4||'')===f);
    const fed=rec&&rec.COFONTEFEDERAL?String(rec.COFONTEFEDERAL).trim():'';
    const label=fed?fed+'.'+f:f;
    const o=document.createElement('option');o.value=f;o.textContent=label;sf.appendChild(o);
  });

  const gnds=[...new Set(TOTAL.map(r=>String(r.GND)))].sort();
  const sg=document.getElementById('fg');
  sg.innerHTML='<option value="">Todos</option>';
  gnds.forEach(g=>{const o=document.createElement('option');o.value=g;o.textContent=g+' — '+nomeGND(g);sg.appendChild(o);});

  aplicar();
}

function nomeGND(g){
  return{1:'Pessoal e Encargos Sociais',2:'Juros e Encargos da Dívida',3:'Outras Despesas Correntes',4:'Investimentos',5:'Inversões Financeiras',6:'Amortização da Dívida'}[Number(g)]||'';
}

/* ── Render hierárquico: Gestão → UG → Fonte+GND ── */
function render(){
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  if(!fil.length){
    tb.innerHTML='<tr><td colspan="10" class="empty">Nenhum registro encontrado.</td></tr>';
    tf.innerHTML='';document.getElementById('cnt').textContent='Nenhum registro';return;
  }

  /* montar árvore: gestão → ug → linhas[] */
  const tree={};
  fil.forEach(r=>{
    const g=String(r.COGESTAO),u=String(r.COUG);
    if(!tree[g])tree[g]={};
    if(!tree[g][u])tree[g][u]=[];
    tree[g][u].push(r);
  });

  const nG=Object.keys(tree).length;
  const nU=new Set(fil.map(r=>r.COUG)).size;
  const nF=fil.length;
  document.getElementById('cnt').textContent=nG+' Gestão/ões · '+nU+' UG'+(nU!==1?'s':'')+' · '+nF.toLocaleString('pt-BR')+' linha'+(nF!==1?'s':'');

  const soma=arr=>({
    VALOR_REPASSE_MES:arr.reduce((s,r)=>s+num(r.VALOR_REPASSE_MES),0),
    VALOR_LIQ_MES:    arr.reduce((s,r)=>s+num(r.VALOR_LIQ_MES),0),
    DIFERENCA_MES:    arr.reduce((s,r)=>s+num(r.DIFERENCA_MES),0),
    VALOR_REPASSE:    arr.reduce((s,r)=>s+num(r.VALOR_REPASSE),0),
    VALOR_LIQ:        arr.reduce((s,r)=>s+num(r.VALOR_LIQ),0),
    DIFERENCA:        arr.reduce((s,r)=>s+num(r.DIFERENCA),0),
  });

  const vcols=t=>'<td class="rv '+vc(t.VALOR_REPASSE_MES)+'">'+brl(t.VALOR_REPASSE_MES)+'</td>'
    +'<td class="rv '+vc(t.VALOR_LIQ_MES)+'">'+brl(t.VALOR_LIQ_MES)+'</td>'
    +'<td class="rv sep-col '+vc(t.DIFERENCA_MES)+'">'+brl(t.DIFERENCA_MES)+'</td>'
    +'<td class="rv '+vc(t.VALOR_REPASSE)+'">'+brl(t.VALOR_REPASSE)+'</td>'
    +'<td class="rv '+vc(t.VALOR_LIQ)+'">'+brl(t.VALOR_LIQ)+'</td>'
    +'<td class="rv sep-col '+vc(t.DIFERENCA)+'">'+brl(t.DIFERENCA)+'</td>';

  let html='';
  Object.keys(tree).sort((a,b)=>Number(a)-Number(b)).forEach(g=>{
    const ugMap=tree[g];
    const todosG=Object.values(ugMap).flat();
    const tg=soma(todosG);
    const nUgG=Object.keys(ugMap).length;
    const gid='g_'+g;
    html+='<tr class="row-gestao" onclick="toggle(\''+gid+'\')">'
      +'<td colspan="4"><span class="tog" id="tog_'+gid+'">▶</span> GESTÃO '+fmtGest(g)
      +' <span style="font-size:10px;opacity:.55">('+nUgG+' UG'+(nUgG!==1?'s':'')+')</span></td>'
      +vcols(tg)+'</tr>';

    Object.keys(ugMap).sort((a,b)=>Number(a)-Number(b)).forEach(u=>{
      const linhas=ugMap[u],tu=soma(linhas),nome=UGS[u]||'';
      const uid=gid+'_u'+u;
      html+='<tr class="row-ug" data-par="'+gid+'" style="display:none" onclick="toggle(\''+uid+'\')">'
        +'<td colspan="4"><span class="tog" id="tog_'+uid+'">▶</span> UG '+u+(nome?' \xb7 '+nome:'')+'</td>'
        +vcols(tu)+'</tr>';

      linhas.sort((a,b)=>String(a.FONTE4||'').localeCompare(String(b.FONTE4||''))||(Number(a.GND)-Number(b.GND))).forEach(r=>{
        const fLabel=fmtFonte(r);
        html+='<tr class="row-fonte" data-par="'+uid+'" style="display:none">'
          +'<td></td>'
          +'<td>'+fLabel+'</td>'
          +'<td>'+r.GND+'</td>'
          +'<td></td>'
          +vcols(r)+'</tr>';
      });
    });
  });
  tb.innerHTML=html;

  const tot=soma(fil);
  tf.innerHTML='<td colspan="4">Total Geral · '+nG+' Gestão/ões · '+nU+' UG'+(nU!==1?'s':'')+' · '+nF.toLocaleString('pt-BR')+' linhas</td>'+vcols(tot);
}

/* ── Toggle ── */
function toggle(id){
  const tog=document.getElementById('tog_'+id);
  if(!tog)return;
  const aberto=tog.dataset.open==='1';
  if(aberto){
    fecharDescendentes(id);
    tog.innerHTML='▶';tog.dataset.open='0';
  } else {
    document.querySelectorAll('[data-par="'+id+'"]').forEach(tr=>tr.style.display='');
    tog.innerHTML='▼';tog.dataset.open='1';
  }
}
function fecharDescendentes(id){
  document.querySelectorAll('[data-par="'+id+'"]').forEach(tr=>{
    tr.style.display='none';
    const ct=tr.querySelector('.tog');
    if(ct&&ct.dataset.open==='1'){ct.innerHTML='▶';ct.dataset.open='0';fecharDescendentes(ct.id.replace('tog_',''));}
  });
}

/* ── KPIs ── */
function kpis(){
  const sm=c=>fil.reduce((a,r)=>a+num(r[c]),0);
  const repMes=sm('VALOR_REPASSE_MES'),liqMes=sm('VALOR_LIQ_MES'),difMes=sm('DIFERENCA_MES');
  const repTot=sm('VALOR_REPASSE'),liqTot=sm('VALOR_LIQ'),difTot=sm('DIFERENCA');
  const dc=t=>Math.abs(t)<0.01?'ko':t>0?'kw':'ka';
  const label=mesSel!==''?(' · '+nomeMes(mesSel)+' '+anoSel):(' '+anoSel);
  document.getElementById('krow').innerHTML=
    '<div class="kpi"><div class="kl">Repassado no Mês'+label+'<span class="acct">961210200 · 300 · 500 · 600</span></div><div class="kv '+vc(repMes)+'">'+brl(repMes)+'</div></div>'
   +'<div class="kpi"><div class="kl">Liquidado no Mês'+label+'<span class="acct">622130300 · 622130400</span></div><div class="kv '+vc(liqMes)+'">'+brl(liqMes)+'</div></div>'
   +'<div class="kpi '+dc(difMes)+'"><div class="kl">Diferença no Mês<span class="acct">Repassado − Liquidado</span></div><div class="kv '+vc(difMes)+'">'+brl(difMes)+'</div></div>'
   +'<div class="kpi"><div class="kl">Total Repassado (Exercício)<span class="acct">961210200 · 300 · 500 · 600</span></div><div class="kv '+vc(repTot)+'">'+brl(repTot)+'</div></div>'
   +'<div class="kpi"><div class="kl">Total Liquidado (Exercício)<span class="acct">622130300 · 622130400</span></div><div class="kv '+vc(liqTot)+'">'+brl(liqTot)+'</div></div>'
   +'<div class="kpi '+dc(difTot)+'"><div class="kl">Diferença Total<span class="acct">Repassado − Liquidado</span></div><div class="kv '+vc(difTot)+'">'+brl(difTot)+'</div></div>';
}

/* ── Exportar CSV ── */
function exportar(){
  if(!fil.length)return alert('Nenhum dado para exportar.');
  const cols=['COGESTAO','COUG','COFONTEFEDERAL','FONTE4','GND','VALOR_REPASSE_MES','VALOR_LIQ_MES','DIFERENCA_MES','VALOR_REPASSE','VALOR_LIQ','DIFERENCA'];
  const hdrs=['Gestao','UG','Fonte Federal','Fonte Gerencial','GND','Repassado no Mes','Liq no Mes','Diferenca Mes','Total Repassado','Total Liquidado','Diferenca Total'];
  const cel=v=>{if(typeof v==='number')return String(v).replace('.',',');const s=v??'';return /^\d+$/.test(s)?`="${s}"`:s;};
  const linhas=[hdrs.join(';')].concat(fil.map(r=>cols.map(c=>cel(r[c])).join(';')));
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),download:'repasses.csv'});
  a.click();URL.revokeObjectURL(a.href);
}
</script>
</body>
</html>
"""

# ── Helpers ────────────────────────────────────────────────────────────────────
def _normalizar(registros):
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

def gerar_html(dados_por_mes: dict, totais: dict, ugs: dict, default_mes: str = "") -> str:
    # dados_por_mes: {"2026_7": [...], "2025_12": [...], ...}
    # totais:        {"2026": [...], "2025": [...]}
    dados_b64  = {chave: _gzip_b64(_normalizar(recs)) for chave, recs in dados_por_mes.items()}
    totais_b64 = {ano: _gzip_b64(_normalizar(recs)) for ano, recs in totais.items()}
    ugs_b64    = _gzip_b64(ugs)
    html = HTML_TEMPLATE
    html = html.replace('{dados_b64}',   json.dumps(dados_b64,  ensure_ascii=False))
    html = html.replace('{totais_b64}',  json.dumps(totais_b64, ensure_ascii=False))
    html = html.replace('{ugs_b64}',     ugs_b64)
    html = html.replace('{default_mes}', default_mes)
    html = html.replace('{timestamp}',   datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    return html

# ── Publicação no GitHub ────────────────────────────────────────────────────────
def publicar_github(html: str) -> str:
    if os.environ.get('NO_GIT_PUSH'):
        return ''
    import requests
    api = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{ARQUIVO_HTML}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    r = requests.get(api, headers=headers)
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {
        "message": f"chore: atualiza repasses — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        "content": base64.b64encode(html.encode("utf-8")).decode(),
        "branch": GITHUB_BRANCH,
    }
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
        filtro_ug = f"AND s.COUG = {int(args.ug)}"
    else:
        filtro_ug = ""

    ANOS = {"2025": "MIL2025.", "2026": "MIL2026."}

    def run(conn, schema, filtro_mes=""):
        s = (SQL.replace("{schema}", schema)
                .replace("{filtro_ug}", filtro_ug)
                .replace("{filtro_mes}", filtro_mes))
        return pd.read_sql(s, conn)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Conectando ao Oracle...")
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:

        dados_por_mes = {}   # chave: "ANO_MES"
        totais        = {}   # chave: "ANO"
        ugs           = {}

        for ano_str, schema in ANOS.items():
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] === Ano {ano_str} (schema {schema}) ===")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Executando consulta acumulada...")
            df_total = run(conn, schema, "")
            totais[ano_str] = df_total.to_dict(orient="records")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {len(df_total):,} registros (Total).")

            for mes in range(15):
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Mês {mes}...", end=" ", flush=True)
                df_mes = run(conn, schema, f"AND s.INMES = {mes}")
                if not df_mes.empty:
                    dados_por_mes[f"{ano_str}_{mes}"] = df_mes.to_dict(orient="records")
                    print(f"{len(df_mes):,} registros.")
                else:
                    print("vazio, ignorado.")

            print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando nomes das UGs ({ano_str})...")
            ugs_df = pd.read_sql(f"SELECT COUG, NOUG FROM {schema}VUNIDADEGESTORA", conn)
            for r in ugs_df.itertuples():
                if r.NOUG:
                    ugs[str(int(r.COUG))] = r.NOUG
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {len(ugs):,} UGs acumuladas.")

    meses_reais_2026 = [int(k.split("_")[1]) for k in dados_por_mes
                        if k.startswith("2026_") and 1 <= int(k.split("_")[1]) <= 12]
    default_mes = str(max(meses_reais_2026)) if meses_reais_2026 else ""

    html = gerar_html(dados_por_mes, totais, ugs, default_mes)
    out = Path(ARQUIVO_HTML)
    out.write_text(html, encoding="utf-8")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] HTML salvo: {ARQUIVO_HTML}")

    if not args.no_push:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Publicando no GitHub...")
        url = publicar_github(html)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Publicado com sucesso.\n  -> {url}")

    print(f"\n-- Resumo ---------------------------------------------------")
    for ano_str in ANOS:
        df_ano = pd.DataFrame(totais.get(ano_str, []))
        if df_ano.empty:
            print(f"  {ano_str}: sem dados")
            continue
        dif_nz = int((df_ano["DIFERENCA"].abs() >= 0.005).sum())
        meses_ano = [k for k in dados_por_mes if k.startswith(ano_str+"_")]
        print(f"  {ano_str}:")
        print(f"    Registros (Total)   : {len(df_ano):,}")
        print(f"    Meses com dados     : {len(meses_ano)}")
        print(f"    UGs                 : {df_ano['COUG'].nunique():,}")
        print(f"    Com diferenca != 0  : {dif_nz:,}")
        print(f"    Total Repassado     : R$ {float(df_ano['VALOR_REPASSE'].sum()):,.2f}")
        print(f"    Total Liquidado     : R$ {float(df_ano['VALOR_LIQ'].sum()):,.2f}")
        print(f"    Diferenca total     : R$ {float(df_ano['DIFERENCA'].sum()):,.2f}")
    print(f"-------------------------------------------------------------\n")

if __name__ == "__main__":
    main()
