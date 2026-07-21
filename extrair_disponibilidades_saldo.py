"""
Disponibilidades por Saldo — SIGGO/Oracle
Consulta VSALDOCONTABIL, agrupando por UG e Fonte.
Gera HTML autocontido e publica no GitHub Pages.

Uso:
    python extrair_disponibilidades_saldo.py
    python extrair_disponibilidades_saldo.py --ug 10101
    python extrair_disponibilidades_saldo.py --no-push
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
SCHEMA      = "MIL2026."

# ── GitHub ─────────────────────────────────────────────────────────────────────
GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
GITHUB_USER   = os.environ["GITHUB_USER"]
GITHUB_REPO   = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = os.environ["GITHUB_BRANCH"]
ARQUIVO_HTML  = "disponibilidades_saldo.html"

# ── SQL ────────────────────────────────────────────────────────────────────────
SQL = """
WITH contas_permitidas AS (
    SELECT DISTINCT COCONTACONTABIL
    FROM {schema}VCONTACONTABIL
    WHERE INSISCONTABIL IN ('F', 'C', 'O')
),
base AS (
    SELECT
        s.COGESTAO          AS COGESTAO,
        s.COUG              AS COUG,
        s.COFONTE           AS COFONTE,
        s.COCONTACONTABIL,
        (s.VACREDITO - s.VADEBITO) AS VALOR_CONTABIL
    FROM {schema}VSALDOCONTABIL s
    INNER JOIN contas_permitidas cp
        ON s.COCONTACONTABIL = cp.COCONTACONTABIL
    {filtro_ug}
    {filtro_mes}
),
saldos AS (
    SELECT
        COGESTAO,
        COUG,
        COFONTE,

        /* AF: contas 1XXXXXXXX (ativo, saldo devedor -> negamos VACREDITO-VADEBITO) */
        ROUND(SUM(
            CASE WHEN COCONTACONTABIL BETWEEN 100000000 AND 199999999
                 THEN -VALOR_CONTABIL ELSE 0 END
        ), 2) AS AF,

        /* PF: contas 22XXXXXXX (passivo financeiro, 200M-229M) */
        ROUND(SUM(
            CASE WHEN COCONTACONTABIL BETWEEN 200000000 AND 229999999
                 THEN VALOR_CONTABIL ELSE 0 END
        ), 2) AS PF,

        /* RPNP: conta 631100000 */
        ROUND(SUM(
            CASE WHEN COCONTACONTABIL = 631100000
                 THEN VALOR_CONTABIL ELSE 0 END
        ), 2) AS RPNP,

        /* (b) Conta 721190300 — conta devedora, saldo = VADEBITO - VACREDITO */
        ROUND(SUM(
            CASE WHEN COCONTACONTABIL = 721190300
                 THEN -VALOR_CONTABIL ELSE 0 END
        ), 2) AS CONTA_721190300

    FROM base
    GROUP BY COGESTAO, COUG, COFONTE
)
SELECT
    s.COGESTAO,
    NVL(g.NOGESTAO, 'Sem nome')                           AS NOGESTAO,
    s.COUG,
    NVL(ug.NOUG, 'Sem nome')                              AS NOUG,
    s.COFONTE,
    NVL(ft.INFONTETESOURO, 'N')  AS INFONTETESOURO,
    NVL(ft.INDESTINACAO,   0)    AS INDESTINACAO,
    s.AF,
    s.PF,
    s.RPNP,
    ROUND(s.AF - s.PF - s.RPNP, 2)                      AS AF_MENOS_PF_RPNP,
    s.CONTA_721190300,
    ROUND((s.AF - s.PF - s.RPNP) - s.CONTA_721190300, 2) AS DIFERENCA
FROM saldos s
LEFT JOIN {schema}FONTERECURSO ft ON ft.COFONTE = s.COFONTE
LEFT JOIN {schema}GESTAO g ON g.COGESTAO = s.COGESTAO
LEFT JOIN {schema}UNIDADEGESTORA ug ON ug.COUG = s.COUG
ORDER BY s.COGESTAO, s.COUG, s.COFONTE
"""

# ── HTML Template ──────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Disponibilidades por Saldo — SIGGO</title>
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
.ac-wrap{position:relative;min-width:200px}
.ac-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text);transition:border-color .15s}
.ac-input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}
.ac-dd{position:absolute;top:calc(100% + 4px);left:0;right:0;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:240px;overflow-y:auto;display:none}
.ac-dd-item{padding:8px 12px;cursor:pointer;font-size:12.5px;border-bottom:1px solid var(--border)}
.ac-dd-item:last-child{border-bottom:none}
.ac-dd-item:hover{background:var(--hover)}
.ac-dd-item strong{color:var(--navy);font-weight:700}
.ac-dd-empty{padding:12px;color:var(--muted);font-size:12px;text-align:center}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto}
.btn{border:none;border-radius:6px;padding:8px 16px;font-size:12px;font-weight:600;cursor:pointer;display:flex;align-items:center;gap:5px;transition:opacity .15s;white-space:nowrap}
.btn:hover{opacity:.85}
.btn-p{background:var(--teal);color:#fff}
.btn-g{background:var(--border);color:var(--text)}
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
tr.row-fonte{background:var(--surface)}
tr.row-fonte:nth-child(even){background:var(--row-alt)}
tr.row-fonte:hover{background:var(--hover)}
tr.row-fonte td{padding:8px 14px 8px 42px;font-size:12.5px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--text)}
tr.row-fonte td:first-child{text-align:left;color:var(--muted)}
tr.row-fonte td.rv{color:var(--navy);font-weight:600}
tfoot tr td{background:#f0f4fb;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;font-size:12.5px;text-align:right;white-space:nowrap}
tfoot tr td:first-child{text-align:left}
.tog{display:inline-block;width:16px;text-align:center;font-size:10px;opacity:.7}
.empty{text-align:center;color:var(--muted);padding:32px;font-size:13px}
</style>
</head>
<body>
<div id="ldg" style="display:none;position:fixed;inset:0;background:rgba(13,27,62,.55);z-index:9999;align-items:center;justify-content:center">
  <div style="background:#fff;border-radius:12px;padding:32px 48px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.25)">
    <div style="font-size:22px;margin-bottom:12px">⏳</div>
    <div style="font-weight:700;color:#0d1b3e;font-size:14px">Carregando dados do mês...</div>
  </div>
</div>

<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">💰</div>
    <h1>DISPONIBILIDADES POR SALDO
      <span>SIGGO · Ano Exercício 2026 · Conta 721190300</span>
    </h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <div id="ts">Gerado em: {timestamp}</div>
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
    <div class="ac-wrap" style="min-width:220px">
      <input id="fu-input" class="ac-input" type="text" placeholder="Código ou nome…" autocomplete="off"
             oninput="onACInput('u')" onfocus="onACFocus('u')" onblur="onACBlur('u')">
      <button class="ac-clear" id="fu-clear" onclick="limparAC('u')" title="Limpar">✕</button>
      <div class="ac-dd" id="fu-dd"></div>
    </div>
  </div>
  <div class="fg">
    <label>Mês</label>
    <select id="fm" onchange="trocarMes(this.value)">
      <option value="">Todos</option>
      <option value="0">0 - Saldo Inicial</option>
      <option value="1">1 - Janeiro</option>
      <option value="2">2 - Fevereiro</option>
      <option value="3">3 - Março</option>
      <option value="4">4 - Abril</option>
      <option value="5">5 - Maio</option>
      <option value="6">6 - Junho</option>
      <option value="7">7 - Julho</option>
      <option value="8">8 - Agosto</option>
      <option value="9">9 - Setembro</option>
      <option value="10">10 - Outubro</option>
      <option value="11">11 - Novembro</option>
      <option value="12">12 - Dezembro</option>
      <option value="13">13 - Encerramento do Exercício</option>
      <option value="14">14 - Encerramento do Exercício</option>
    </select>
  </div>
  <div class="fg">
    <label>Fonte</label>
    <select id="ff" onchange="aplicar()"><option value="">Todas</option></select>
  </div>
  <div class="fg">
    <label>Fonte Tesouro</label>
    <select id="fft" onchange="aplicar()">
      <option value="">Todos</option>
      <option value="S">Sim</option>
      <option value="N">Não</option>
    </select>
  </div>
  <div class="fg">
    <label>Destinação do Recurso</label>
    <select id="fdr" onchange="aplicar()">
      <option value="">Todos</option>
      <option value="0">0 · Não Atribuído</option>
      <option value="1">1 · Ordinário</option>
      <option value="2">2 · Vinculado</option>
      <option value="3">3 · Extraordinário</option>
    </select>
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
  <div class="btns">
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
    <thead><tr>
      <th style="width:300px;text-align:left">Gestão / UG / Fonte</th>
      <th style="width:90px;text-align:left">Mês</th>
      <th style="width:120px">Ativo Financeiro (AF)</th>
      <th style="width:120px">Passivo Financeiro (PF)</th>
      <th style="width:100px">RPNP</th>
      <th style="width:130px">AF−(PF+RPNP) (a)</th>
      <th style="width:130px">Conta 721190300 (b)</th>
      <th style="width:130px">Diferença (a−b)</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
    <tfoot><tr id="tfoot"></tr></tfoot>
  </table>
</div>

<script>
const DADOS_B64={dados_b64};
let ALL=[],CACHE={},mesSel='',fil=[];
let gestaoList=[],ugList=[],acState={};
const NOMES_MES=['Saldo Inicial','Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro','Encerramento do Exercício','Encerramento do Exercício'];
const nomeMes=m=>{const n=Number(m);return(n>=0&&n<=14)?n+' · '+NOMES_MES[n]:String(m);};
const brl=v=>{if(isNaN(v))return'—';const r=Math.round(Number(v)*100)/100;return(r||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});};
const vc=v=>Math.abs(v)<0.005?'vz':v>0?'vp':'vn';

async function decomp(b64){
  const bin=atob(b64),bytes=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
  const ds=new DecompressionStream('gzip');
  const writer=ds.writable.getWriter();
  writer.write(bytes);writer.close();
  const chunks=[];
  const reader=ds.readable.getReader();
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
  CACHE[mes]=data;
  return data;
}

(async()=>{
  try{
    const todosData=await decomp(DADOS_B64['']);
    CACHE['']=todosData;ALL=todosData;
  }catch(e){
    console.error('Erro ao descomprimir:',e);
    document.getElementById('ldg').innerHTML='<div style="color:#c0392b;padding:40px;text-align:center">Erro ao carregar dados.<br>Recarregue a página.</div>';
    return;
  }
  document.getElementById('ldg').style.display='none';
  init();
})();

/* ── Autocomplete (Gestão / UG) ── */
function onACInput(k){
  const st=acState[k],v=document.getElementById(st.inp).value.toLowerCase();
  const m=v?st.list.filter(x=>x.c.includes(v)||x.label.toLowerCase().includes(v)):st.list;
  renderACDD(k,m);if(!v){st.sel='';document.getElementById(st.clr).style.display='none';}
}
function onACFocus(k){
  const st=acState[k],v=document.getElementById(st.inp).value.toLowerCase();
  renderACDD(k,v?st.list.filter(x=>x.c.includes(v)||x.label.toLowerCase().includes(v)):st.list);
}
function onACBlur(k){setTimeout(()=>document.getElementById(acState[k].dd).style.display='none',200);}
function renderACDD(k,lista){
  const dd=document.getElementById(acState[k].dd);
  if(!lista.length){dd.innerHTML='<div class="ac-dd-empty">Nenhum resultado</div>';dd.style.display='block';return;}
  dd.innerHTML=lista.slice(0,100).map(x=>{
    const sep=x.label.indexOf(' · ');
    const disp=sep>=0?'<strong>'+x.label.slice(0,sep)+'</strong> · '+x.label.slice(sep+3):x.label;
    return '<div class="ac-dd-item" onmousedown="selAC(\''+k+'\',\''+x.c+'\',\''+x.label.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\')">'+disp+'</div>';
  }).join('');
  dd.style.display='block';
}
function selAC(k,c,label){
  const st=acState[k];st.sel=c;
  document.getElementById(st.inp).value=label;
  document.getElementById(st.dd).style.display='none';
  document.getElementById(st.clr).style.display='block';
  aplicar();
}
function limparAC(k){
  const st=acState[k];st.sel='';
  document.getElementById(st.inp).value='';
  document.getElementById(st.clr).style.display='none';
  document.getElementById(st.dd).style.display='none';
  aplicar();
}
document.addEventListener('click',function(e){if(!e.target.closest('.ac-wrap')){document.getElementById('fg-dd').style.display='none';document.getElementById('fu-dd').style.display='none';}});

/* ── Filtros ── */
function aplicar(){
  const ff=document.getElementById('ff').value;
  const sd=document.getElementById('fs').value;
  const fft=document.getElementById('fft').value;
  const fdr=document.getElementById('fdr').value;
  fil=ALL.filter(r=>{
    if(acState['g']&&acState['g'].sel&&String(r.COGESTAO)!==acState['g'].sel)return false;
    if(acState['u']&&acState['u'].sel&&String(r.COUG)!==acState['u'].sel)return false;
    if(ff&&String(r.COFONTE)!==ff)return false;
    if(fft&&String(r.INFONTETESOURO)!==fft)return false;
    if(fdr!==''&&String(r.INDESTINACAO)!==fdr)return false;
    if(sd==='dif_pos'&&r.DIFERENCA<=0)return false;
    if(sd==='dif_neg'&&r.DIFERENCA>=0)return false;
    if(sd==='dif_nz'&&Math.abs(r.DIFERENCA)<0.005)return false;
    return true;
  });
  render();kpis();
}
async function trocarMes(mes){
  mesSel=mes;
  document.getElementById('ldg').style.display='flex';
  ALL=await carregarMes(mes);
  document.getElementById('ldg').style.display='none';
  initFiltros();
}
function limpar(){
  document.getElementById('fm').value='';mesSel='';ALL=CACHE['']||[];
  document.getElementById('ff').value='';document.getElementById('fs').value='todos';
  document.getElementById('fft').value='';document.getElementById('fdr').value='';
  ['g','u'].forEach(k=>{if(acState[k]){const st=acState[k];st.sel='';document.getElementById(st.inp).value='';document.getElementById(st.clr).style.display='none';document.getElementById(st.dd).style.display='none';}});
  initFiltros();
}

function initFiltros(){
  const fontes=[...new Set(ALL.map(r=>String(r.COFONTE)))].sort();
  const sf=document.getElementById('ff');
  const prevFonte=sf.value;
  sf.innerHTML='<option value="">Todas</option>';
  fontes.forEach(f=>{const o=document.createElement('option');o.value=f;o.textContent=f;sf.appendChild(o);});
  sf.value=prevFonte;
  aplicar();
}
function buildList(keyFn,labelFn){
  const m={};
  ALL.forEach(r=>{const k=keyFn(r);if(k&&!m[k])m[k]=labelFn(r);});
  return Object.entries(m).map(([c,label])=>({c,label})).sort((a,b)=>a.c.localeCompare(b.c,'pt-BR'));
}
function init(){
  ALL.forEach(r=>{
    r.GESTAO_LABEL=r.COGESTAO+(r.NOGESTAO&&r.NOGESTAO!=='Sem nome'?' · '+r.NOGESTAO:'');
    r.UG_LABEL=r.COUG+(r.NOUG&&r.NOUG!=='Sem nome'?' · '+r.NOUG:'');
  });
  gestaoList=buildList(r=>String(r.COGESTAO),r=>r.GESTAO_LABEL);
  ugList=buildList(r=>String(r.COUG),r=>r.UG_LABEL);
  acState={
    'g':{sel:'',list:gestaoList,inp:'fg-input',clr:'fg-clear',dd:'fg-dd'},
    'u':{sel:'',list:ugList,    inp:'fu-input',clr:'fu-clear',dd:'fu-dd'}
  };
  initFiltros();
}

/* ── Helpers de soma e células ── */
function soma(arr){
  return{AF:arr.reduce((s,r)=>s+r.AF,0),PF:arr.reduce((s,r)=>s+r.PF,0),
    RPNP:arr.reduce((s,r)=>s+r.RPNP,0),AF_MENOS_PF_RPNP:arr.reduce((s,r)=>s+r.AF_MENOS_PF_RPNP,0),
    CONTA_721190300:arr.reduce((s,r)=>s+r.CONTA_721190300,0),DIFERENCA:arr.reduce((s,r)=>s+r.DIFERENCA,0)};
}
function valCols(t){
  return '<td class="rv '+vc(t.AF)+'">'+brl(t.AF)+'</td>'
        +'<td class="rv '+vc(t.PF)+'">'+brl(t.PF)+'</td>'
        +'<td class="rv '+vc(t.RPNP)+'">'+brl(t.RPNP)+'</td>'
        +'<td class="rv '+vc(t.AF_MENOS_PF_RPNP)+'">'+brl(t.AF_MENOS_PF_RPNP)+'</td>'
        +'<td class="rv '+vc(t.CONTA_721190300)+'">'+brl(t.CONTA_721190300)+'</td>'
        +'<td class="rv '+vc(t.DIFERENCA)+'">'+brl(t.DIFERENCA)+'</td>';
}

/* ── Render hierárquico ── */
function render(){
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  if(!fil.length){
    tb.innerHTML='<tr><td colspan="8" class="empty">Nenhum registro encontrado.</td></tr>';
    tf.innerHTML='';document.getElementById('cnt').textContent='Nenhum registro';return;
  }

  /* montar árvore gestao -> ug -> fontes[] */
  const tree={};
  fil.forEach(r=>{
    const g=String(r.COGESTAO),u=String(r.COUG);
    if(!tree[g])tree[g]={};
    if(!tree[g][u])tree[g][u]=[];
    tree[g][u].push(r);
  });

  const nF=fil.length,nU=new Set(fil.map(r=>r.COUG)).size,nG=Object.keys(tree).length;
  document.getElementById('cnt').textContent=nG+' Gestão/ões · '+nU+' UGs · '+nF.toLocaleString('pt-BR')+' fonte'+(nF!==1?'s':'');


  let html='';
  Object.keys(tree).sort().forEach(g=>{
    const ugMap=tree[g];
    const todosG=Object.values(ugMap).flat();
    const tg=soma(todosG);
    const nUgG=Object.keys(ugMap).length;
    const gid='g_'+g;
    const nomeG=todosG[0]&&todosG[0].NOGESTAO&&todosG[0].NOGESTAO!=='Sem nome'?todosG[0].NOGESTAO:'';
    html+='<tr class="row-gestao" onclick="toggle(\''+gid+'\')">'
         +'<td><span class="tog" id="tog_'+gid+'">▶</span> GESTÃO '+g+(nomeG?' · '+nomeG:'')
         +' <span style="font-size:10px;opacity:.55">('+nUgG+' UG'+(nUgG!==1?'s':'')+')</span></td>'
         +'<td></td>'
         +valCols(tg)+'</tr>';

    Object.keys(ugMap).sort((a,b)=>Number(a)-Number(b)).forEach(u=>{
      const fontes=ugMap[u],tu=soma(fontes);
      const nome=fontes[0]&&fontes[0].NOUG&&fontes[0].NOUG!=='Sem nome'?fontes[0].NOUG:'';
      const uid=gid+'_u'+u;
      html+='<tr class="row-ug" data-par="'+gid+'" style="display:none" onclick="toggle(\''+uid+'\')">'
           +'<td><span class="tog" id="tog_'+uid+'">▶</span> UG '+u+(nome?' · '+nome:'')
           +' <span style="font-size:10px;opacity:.5">('+fontes.length+' fonte'+(fontes.length!==1?'s':'')+')</span></td>'
           +'<td></td>'
           +valCols(tu)+'</tr>';

      fontes.forEach(r=>{
        html+='<tr class="row-fonte" data-par="'+uid+'" style="display:none">'
             +'<td>Fonte '+r.COFONTE+'</td>'
             +'<td style="text-align:left;color:var(--text)">'+(mesSel!==''?nomeMes(mesSel):'—')+'</td>'
             +'<td class="rv '+vc(r.AF)+'">'+brl(r.AF)+'</td>'
             +'<td class="rv '+vc(r.PF)+'">'+brl(r.PF)+'</td>'
             +'<td class="rv '+vc(r.RPNP)+'">'+brl(r.RPNP)+'</td>'
             +'<td class="rv '+vc(r.AF_MENOS_PF_RPNP)+'">'+brl(r.AF_MENOS_PF_RPNP)+'</td>'
             +'<td class="rv '+vc(r.CONTA_721190300)+'">'+brl(r.CONTA_721190300)+'</td>'
             +'<td class="rv '+vc(r.DIFERENCA)+'">'+brl(r.DIFERENCA)+'</td>'
             +'</tr>';
      });
    });
  });
  tb.innerHTML=html;

  const tot=soma(fil);
  tf.innerHTML='<td>Total Geral · '+nG+' Gestão/ões · '+nU+' UGs · '+nF.toLocaleString('pt-BR')+' fontes</td>'
    +'<td></td>'
    +'<td class="'+vc(tot.AF)+'">'+brl(tot.AF)+'</td>'
    +'<td class="'+vc(tot.PF)+'">'+brl(tot.PF)+'</td>'
    +'<td class="'+vc(tot.RPNP)+'">'+brl(tot.RPNP)+'</td>'
    +'<td class="'+vc(tot.AF_MENOS_PF_RPNP)+'">'+brl(tot.AF_MENOS_PF_RPNP)+'</td>'
    +'<td class="'+vc(tot.CONTA_721190300)+'">'+brl(tot.CONTA_721190300)+'</td>'
    +'<td class="'+vc(tot.DIFERENCA)+'">'+brl(tot.DIFERENCA)+'</td>';
}

/* ── Toggle ── */
function toggle(id){
  const tog=document.getElementById('tog_'+id);
  const aberto=tog&&tog.textContent.trim()==='▼';
  if(aberto){
    /* fechar filhos diretos e recursivamente */
    fecharDescendentes(id);
    if(tog)tog.textContent='▶';
  } else {
    /* abrir apenas filhos diretos */
    document.querySelectorAll('[data-par="'+id+'"]').forEach(tr=>tr.style.display='');
    if(tog)tog.textContent='▼';
  }
}
function fecharDescendentes(id){
  document.querySelectorAll('[data-par="'+id+'"]').forEach(tr=>{
    tr.style.display='none';
    /* se era um nó pai (row-ug ou row-gestao), fechar seus filhos recursivamente */
    const childId=tr.id? tr.id.replace('','') : null;
    /* extrair o id do nó a partir do tog dentro dele */
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
function kpis(){
  const sm=c=>fil.reduce((a,r)=>a+r[c],0);
  const dif=sm('DIFERENCA'),cd=fil.filter(r=>Math.abs(r.DIFERENCA)>=0.005).length;
  const pct=fil.length?((cd/fil.length)*100).toFixed(1):'0.0';
  const a_val=sm('AF_MENOS_PF_RPNP');
  const dc=Math.abs(dif)<0.01?'ko':dif>0?'kw':'ka';
  document.getElementById('krow').innerHTML=
    '<div class="kpi"><div class="kl">Ativo Financeiro (AF)</div><div class="kv '+vc(sm('AF'))+'">'+brl(sm('AF'))+'</div><div class="ks">Contas 1XXXXXXXX (F)</div></div>'
   +'<div class="kpi"><div class="kl">Passivo Financeiro (PF)</div><div class="kv '+vc(sm('PF'))+'">'+brl(sm('PF'))+'</div><div class="ks">Contas 22XXXXXXX (F)</div></div>'
   +'<div class="kpi"><div class="kl">RPNP</div><div class="kv '+vc(sm('RPNP'))+'">'+brl(sm('RPNP'))+'</div><div class="ks">Conta 631100000</div></div>'
   +'<div class="kpi"><div class="kl">AF − (PF + RPNP) · coluna (a)</div><div class="kv '+vc(a_val)+'">'+brl(a_val)+'</div><div class="ks">Equilíbrio esperado = Conta 721190300</div></div>'
   +'<div class="kpi"><div class="kl">Conta 721190300 (b)</div><div class="kv '+vc(sm('CONTA_721190300'))+'">'+brl(sm('CONTA_721190300'))+'</div><div class="ks">Disponibilidades</div></div>'
   +'<div class="kpi '+dc+'"><div class="kl">Diferença (a − b)</div><div class="kv '+vc(dif)+'">'+brl(dif)+'</div>'
   +'<div class="ks"><span class="badge '+(cd>0?'br':'bg')+'">'+cd.toLocaleString('pt-BR')+' fontes c/ dif. · '+pct+'%</span></div></div>';
}

/* ── Exportar CSV ── */
function exportar(){
  if(!fil.length)return alert('Nenhum dado para exportar.');
  const mes=mesSel!==''?nomeMes(mesSel):'Todos';
  const cols=['COGESTAO','COUG','COFONTE','INFONTETESOURO','INDESTINACAO','AF','PF','RPNP','AF_MENOS_PF_RPNP','CONTA_721190300','DIFERENCA'];
  const hdrs=['Gestao','Unidade Gestora','Fonte','Fonte Tesouro','Destinacao Recurso','AF','PF','RPNP','AF-(PF+RPNP)','Conta 721190300','Diferenca'];
  const cel=v=>{if(typeof v==='number')return String(v).replace('.',',');const s=v??'';return /^\d+$/.test(s)?`="${s}"`:s;};
  const linhasMes=fil.map(r=>[...cols.map(c=>cel(r[c])),mes]);
  const linhas=[hdrs.concat(['Mes']).join(';')].concat(linhasMes.map(r=>r.join(';')));
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),download:'disponibilidades_saldo.csv'});
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
            try:
                row[k] = float(v) if hasattr(v, "__float__") else str(v)
            except Exception:
                row[k] = str(v)
        out.append(row)
    return out

def _gzip_b64(data) -> str:
    j = json.dumps(data, ensure_ascii=False)
    return base64.b64encode(gzip.compress(j.encode("utf-8"), compresslevel=9)).decode("ascii")

# ── Geração do HTML ─────────────────────────────────────────────────────────────
def gerar_html(dados_por_mes: dict) -> str:
    dados_b64 = {mes: _gzip_b64(_normalizar(recs)) for mes, recs in dados_por_mes.items()}
    html = HTML_TEMPLATE
    html = html.replace('{dados_b64}', json.dumps(dados_b64, ensure_ascii=False))
    html = html.replace('{timestamp}', datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    return html

# ── Publicação no GitHub ────────────────────────────────────────────────────────
def publicar_github(html: str) -> str:
    import requests
    api = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{ARQUIVO_HTML}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    r = requests.get(api, headers=headers)
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {"message": f"chore: atualiza disponibilidades por saldo — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
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

    filtro_ug_sql = "WHERE s.COUG = :ug" if args.ug else ""
    params        = {"ug": int(args.ug)} if args.ug else {}

    def run(filtro_mes=""):
        sql = (SQL
               .replace("{schema}",    SCHEMA)
               .replace("{filtro_ug}", filtro_ug_sql)
               .replace("{filtro_mes}", filtro_mes))
        return pd.read_sql(sql, conn, params=params if params else None)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Conectando ao Oracle...")
    dados_por_mes = {}
    df_todos = None

    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        # Consulta "Todos" — sem filtro de mês (igual ao original, valores corretos)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Todos os meses...")
        df_todos = run("")
        dados_por_mes[""] = df_todos.to_dict(orient="records")
        print(f"  {len(df_todos):,} registros.")

        # Consulta por mês (0–14)
        for mes in range(15):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Mês {mes}...", end=" ")
            try:
                df_mes = run(f"AND s.INMES = {mes}")
                if not df_mes.empty:
                    dados_por_mes[str(mes)] = df_mes.to_dict(orient="records")
                    print(f"{len(df_mes):,} registros.")
                else:
                    print("sem dados.")
            except Exception as e:
                print(f"erro: {e}")

    html = gerar_html(dados_por_mes)
    out = Path(ARQUIVO_HTML)
    out.write_text(html, encoding="utf-8")
    tamanho_mb = out.stat().st_size / 1_048_576
    print(f"[{datetime.now().strftime('%H:%M:%S')}] HTML salvo: {ARQUIVO_HTML} ({tamanho_mb:.1f} MB)")

    if not args.no_push:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Publicando no GitHub...")
        url = publicar_github(html)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Publicado com sucesso.\n  -> {url}")

    dif_nz = int((df_todos["DIFERENCA"].abs() >= 0.005).sum())
    print(f"\n-- Resumo (Todos os meses) ----------------------------------")
    print(f"  Registros           : {len(df_todos):,}")
    print(f"  UGs                 : {df_todos['COUG'].nunique():,}")
    print(f"  Com diferenca != 0  : {dif_nz:,}")
    print(f"  Diferenca total     : R$ {float(df_todos['DIFERENCA'].sum()):,.2f}")
    print(f"  Meses com dados     : {[k for k in dados_por_mes if k != '']}")
    print(f"-------------------------------------------------------------\n")

if __name__ == "__main__":
    main()
