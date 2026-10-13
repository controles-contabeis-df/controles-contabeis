"""
Superávit a Devolver — Disponibilidade Real por Fonte (conta 721190200) — SIGGO/Oracle
Consulta VSALDOCONTABIL na conta 721190200 (Disponibilidades Real por Fonte de Recursos),
agrupando por Gestão, UG e Fonte, e gera HTML autocontido.

A fonte considerada é a COCONTACORRENTE da conta 721190200 (na conta, o detalhamento é a
própria fonte), não a COFONTE da linha. Se a conta corrente não for numérica de 9 dígitos,
cai para a COFONTE.

Regras de seleção (definidas pelo setor de contabilidade):
  - Somente fontes cujo 1º dígito é 2 ou 4.
  - Exceções de fonte: 237000000, 437000000, 248000000, 448000000.
  - Exceções de fonte por prefixo (convênios): 221, 421, 231, 431, 232, 432.
  - Exceções de UG (LC Distrital 925/2017): Codhab, Inas, Iprev e fundos do Iprev.

Uso:
    python extrair_superavit_a_devolver.py
    python extrair_superavit_a_devolver.py --ug 10101
    python extrair_superavit_a_devolver.py --no-push   (gera HTML sem publicar)
"""

import argparse
import base64
import gzip
import json
import os
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
ORACLE_DSN  = os.environ["ORACLE_DSN"]
SCHEMA      = "MIL2026."

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_USER   = os.environ.get("GITHUB_USER", "")
GITHUB_REPO   = os.environ.get("GITHUB_REPO", "")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")

ARQUIVO_HTML = "superavit_a_devolver.html"
CONTA        = 721190200

# ── Regras de exceção ──────────────────────────────────────────────────────────
PRIMEIRO_DIGITO_FONTE = ("2", "4")

FONTES_EXCECAO = (237000000, 437000000, 248000000, 448000000)

# Fontes de convênio (3 primeiros dígitos)
PREFIXOS_FONTE_EXCECAO = ("221", "421", "231", "431", "232", "432")

# UGs excluídas por força da LC Distrital 925/2017 (UG, Gestão, Nome)
UGS_EXCECAO = (
    (280209, 28209, "Codhab"),
    (140202, 14202, "Inas"),
    (320201, 32203, "Iprev"),
    (320202, 32203, "FSG Iprev"),
    (320203, 32203, "Fundo Financeiro Iprev"),
    (320204, 32203, "Fundo Capitalizado Iprev"),
    (320206, 32203, "Novo Fundo Capitalizado Iprev"),
)
UGS_EXCECAO_COD = tuple(sorted({u for u, _, _ in UGS_EXCECAO}))

# ── SQL ────────────────────────────────────────────────────────────────────────
SQL = """
WITH base AS (
    SELECT
        s.COGESTAO,
        s.COUG,
        s.INMES,
        /* Fonte = conta corrente da 721190200 (9 dígitos); senão, COFONTE da linha */
        CASE
            WHEN REGEXP_LIKE(TRIM(TO_CHAR(s.COCONTACORRENTE)), '^[0-9]{9}$')
            THEN TO_NUMBER(TRIM(TO_CHAR(s.COCONTACORRENTE)))
            ELSE s.COFONTE
        END AS COFONTE,
        s.VADEBITO,
        s.VACREDITO
    FROM {schema}VSALDOCONTABIL s
    WHERE s.COCONTACONTABIL = {conta}
      AND s.COUG NOT IN ({ugs_exc})
      {filtro_ug}
      {filtro_mes}
)
SELECT
    b.COGESTAO,
    NVL(g.NOGESTAO, 'Sem nome')   AS NOGESTAO,
    b.COUG,
    NVL(ug.NOUG, 'Sem nome')      AS NOUG,
    b.COFONTE,
    NVL(ft.NOFONTE, 'Sem nome')   AS NOFONTE,
    ROUND(SUM(b.VADEBITO) - SUM(b.VACREDITO), 2)  AS SALDO
FROM base b
LEFT JOIN {schema}GESTAO g          ON g.COGESTAO  = b.COGESTAO
LEFT JOIN {schema}UNIDADEGESTORA ug ON ug.COUG     = b.COUG
LEFT JOIN {schema}FONTERECURSO ft   ON ft.COFONTE  = b.COFONTE
WHERE SUBSTR(TO_CHAR(b.COFONTE), 1, 1) IN ({digitos})
  AND b.COFONTE NOT IN ({fontes_exc})
  AND SUBSTR(TO_CHAR(b.COFONTE), 1, 3) NOT IN ({prefixos_exc})
GROUP BY b.COGESTAO, g.NOGESTAO, b.COUG, ug.NOUG, b.COFONTE, ft.NOFONTE
ORDER BY b.COGESTAO, b.COUG, b.COFONTE
"""

# ── HTML Template ──────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Superávit a Devolver — SIGGO</title>
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
.aviso{background:#fff8e6;border-bottom:1px solid #f0c060;padding:8px 28px;font-size:11.5px;color:#7a5c00;display:flex;align-items:flex-start;gap:8px;line-height:1.5}
.aviso details{flex:1}
.aviso summary{cursor:pointer;font-weight:600}
.aviso ul{margin:6px 0 2px 18px}
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
table{width:100%;border-collapse:collapse;table-layout:fixed;min-width:900px}
thead th{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:right;white-space:nowrap;user-select:none;letter-spacing:.3px;overflow:hidden;text-overflow:ellipsis}
thead th.l{text-align:left}
.vp{font-weight:600}
.vn{font-weight:600}
/* ── Hierarquia ── */
tr.row-gestao{background:#1e3267;cursor:pointer}
tr.row-gestao:hover{background:#162550}
tr.row-gestao td{color:#e8f0fc;font-weight:700;padding:10px 14px;font-size:12px;border-bottom:2px solid #0d1b3e;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.row-gestao td:first-child{text-align:left}
tr.row-ug{background:#2a4a7f;cursor:pointer}
tr.row-ug:hover{background:#243f6e}
tr.row-ug td{color:#dce8f8;font-weight:700;padding:9px 14px 9px 26px;font-size:12px;border-bottom:1.5px solid #1e3267;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.row-ug td:first-child{text-align:left}
tr.row-fonte{background:var(--surface)}
tr.row-fonte:nth-child(even){background:var(--row-alt)}
tr.row-fonte:hover{background:var(--hover)}
tr.row-fonte td{padding:8px 14px 8px 42px;font-size:12.5px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;color:var(--text)}
tr.row-fonte td:first-child{text-align:left;color:var(--muted)}
tr.row-fonte td.rv{font-weight:600}
tr.row-fonte td.vp{color:var(--green)}
tr.row-fonte td.vn{color:var(--red)}
tr.row-fonte td.vz{color:var(--muted)}
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
    <div class="hlogo">↩️</div>
    <h1>SUPERÁVIT A DEVOLVER
      <span>SIGGO · Ano Exercício 2026 · Disponibilidade Real · Conta 721190200</span>
    </h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <div id="ts">Gerado em: {timestamp}</div>
</header>

<div class="aviso">
  <span>ℹ️</span>
  <details>
    <summary>Critérios de seleção — fontes iniciadas por 2 ou 4, exceto fontes de convênio, fontes específicas e UGs amparadas pela LC Distrital 925/2017</summary>
    <ul>
      <li><strong>Fontes consideradas:</strong> 1º dígito igual a 2 ou 4.</li>
      <li><strong>Fontes excluídas:</strong> {fontes_exc_txt}.</li>
      <li><strong>Fontes de convênio excluídas (3 primeiros dígitos):</strong> {prefixos_exc_txt}.</li>
      <li><strong>UGs excluídas (LC Distrital 925/2017):</strong> {ugs_exc_txt}.</li>
    </ul>
  </details>
</div>

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
    <label>Exibir saldos</label>
    <select id="fs" onchange="aplicar()">
      <option value="todos">Todos</option>
      <option value="nz" selected>Com saldo</option>
      <option value="pos">Saldo positivo</option>
      <option value="neg">Saldo negativo</option>
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
      <th class="l" style="width:300px">Gestão / UG / Fonte</th>
      <th class="l" style="width:340px">Nome da Fonte</th>
      <th class="l" style="width:150px">Mês</th>
      <th style="width:190px">Disponibilidade Real (R$)</th>
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
const fmtGestao=v=>String(v).padStart(5,'0');
const brl=v=>{if(isNaN(v))return'—';const r=Math.round(Number(v)*100)/100;return(r||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});};
const num=v=>{if(isNaN(v))return'—';const r=Math.round(Number(v)*100)/100;return(r||0).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});};
const vc=v=>Math.abs(v)<0.005?'vz':v>0?'vp':'vn';
const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

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
    document.getElementById('ldg').style.display='flex';
    document.getElementById('ldg').innerHTML='<div style="background:#fff;color:#c0392b;padding:40px;text-align:center;border-radius:12px">Erro ao carregar dados.<br>Recarregue a página.</div>';
    return;
  }
  init();
})();

/* ── Autocomplete (Gestão / UG) ── */
function onACInput(k){
  const st=acState[k],v=document.getElementById(st.inp).value.toLowerCase();
  const m=v?st.list.filter(x=>x.c.includes(v)||x.label.toLowerCase().includes(v)):st.list;
  renderACDD(k,m);if(!v){st.sel='';document.getElementById(st.clr).style.display='none';aplicar();}
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
    const disp=sep>=0?'<strong>'+esc(x.label.slice(0,sep))+'</strong> · '+esc(x.label.slice(sep+3)):esc(x.label);
    return '<div class="ac-dd-item" data-k="'+k+'" data-c="'+esc(x.c)+'">'+disp+'</div>';
  }).join('');
  dd.querySelectorAll('.ac-dd-item').forEach(el=>{
    el.addEventListener('mousedown',()=>{
      const it=acState[el.dataset.k].list.find(x=>x.c===el.dataset.c);
      selAC(el.dataset.k,it.c,it.label);
    });
  });
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

/* ── Filtros ── */
function aplicar(){
  const ff=document.getElementById('ff').value;
  const sd=document.getElementById('fs').value;
  fil=ALL.filter(r=>{
    if(acState['g']&&acState['g'].sel&&fmtGestao(r.COGESTAO)!==acState['g'].sel)return false;
    if(acState['u']&&acState['u'].sel&&String(r.COUG)!==acState['u'].sel)return false;
    if(ff&&String(r.COFONTE)!==ff)return false;
    if(sd==='pos'&&r.SALDO<=0.005)return false;
    if(sd==='neg'&&r.SALDO>=-0.005)return false;
    if(sd==='nz'&&Math.abs(r.SALDO)<0.005)return false;
    return true;
  });
  render();kpis();
}
async function trocarMes(mes){
  mesSel=mes;
  document.getElementById('ldg').style.display='flex';
  ALL=await carregarMes(mes);
  document.getElementById('ldg').style.display='none';
  prepararListas();
  initFiltros();
}
function limpar(){
  document.getElementById('fm').value='';mesSel='';ALL=CACHE['']||[];
  document.getElementById('ff').value='';document.getElementById('fs').value='nz';
  ['g','u'].forEach(k=>{const st=acState[k];st.sel='';document.getElementById(st.inp).value='';document.getElementById(st.clr).style.display='none';document.getElementById(st.dd).style.display='none';});
  prepararListas();
  initFiltros();
}

function initFiltros(){
  const fontes=[...new Set(ALL.map(r=>String(r.COFONTE)))].sort();
  const sf=document.getElementById('ff');
  const prevFonte=sf.value;
  sf.innerHTML='<option value="">Todas</option>';
  fontes.forEach(f=>{const o=document.createElement('option');o.value=f;o.textContent=f;sf.appendChild(o);});
  sf.value=fontes.includes(prevFonte)?prevFonte:'';
  aplicar();
}
function buildList(keyFn,labelFn){
  const m={};
  ALL.forEach(r=>{const k=keyFn(r);if(k&&!m[k])m[k]=labelFn(r);});
  return Object.entries(m).map(([c,label])=>({c,label})).sort((a,b)=>a.c.localeCompare(b.c,'pt-BR'));
}
function prepararListas(){
  ALL.forEach(r=>{
    r.GESTAO_LABEL=fmtGestao(r.COGESTAO)+(r.NOGESTAO&&r.NOGESTAO!=='Sem nome'?' · '+r.NOGESTAO:'');
    r.UG_LABEL=r.COUG+(r.NOUG&&r.NOUG!=='Sem nome'?' · '+r.NOUG:'');
  });
  const g=acState['g'],u=acState['u'];
  g.list=buildList(r=>fmtGestao(r.COGESTAO),r=>r.GESTAO_LABEL);
  u.list=buildList(r=>String(r.COUG),r=>r.UG_LABEL);
}
function init(){
  acState={
    'g':{sel:'',list:[],inp:'fg-input',clr:'fg-clear',dd:'fg-dd'},
    'u':{sel:'',list:[],inp:'fu-input',clr:'fu-clear',dd:'fu-dd'}
  };
  prepararListas();
  initFiltros();
}

/* ── Helpers de soma e células ── */
function soma(arr){
  return{SALDO:arr.reduce((s,r)=>s+r.SALDO,0)};
}
function valCols(t){
  return '<td class="rv '+vc(t.SALDO)+'">'+num(t.SALDO)+'</td>';
}

/* ── Render hierárquico ── */
function render(){
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  if(!fil.length){
    tb.innerHTML='<tr><td colspan="4" class="empty">Nenhum registro encontrado.</td></tr>';
    tf.innerHTML='';document.getElementById('cnt').textContent='Nenhum registro';return;
  }

  const tree={};
  fil.forEach(r=>{
    const g=fmtGestao(r.COGESTAO),u=String(r.COUG);
    if(!tree[g])tree[g]={};
    if(!tree[g][u])tree[g][u]=[];
    tree[g][u].push(r);
  });

  const nF=fil.length,nU=new Set(fil.map(r=>r.COUG)).size,nG=Object.keys(tree).length;
  document.getElementById('cnt').textContent=nG+' Gestão/ões · '+nU+' UGs · '+nF.toLocaleString('pt-BR')+' fonte'+(nF!==1?'s':'');

  const mesTxt=mesSel!==''?nomeMes(mesSel):'—';
  let html='';
  Object.keys(tree).sort().forEach(g=>{
    const ugMap=tree[g];
    const todosG=Object.values(ugMap).flat();
    const tg=soma(todosG);
    const nUgG=Object.keys(ugMap).length;
    const gid='g_'+g;
    const nomeG=todosG[0]&&todosG[0].NOGESTAO&&todosG[0].NOGESTAO!=='Sem nome'?todosG[0].NOGESTAO:'';
    html+='<tr class="row-gestao" onclick="toggle(\''+gid+'\')">'
         +'<td><span class="tog" id="tog_'+gid+'">▶</span> GESTÃO '+g+(nomeG?' · '+esc(nomeG):'')
         +' <span style="font-size:10px;opacity:.55">('+nUgG+' UG'+(nUgG!==1?'s':'')+')</span></td>'
         +'<td></td><td></td>'+valCols(tg)+'</tr>';

    Object.keys(ugMap).sort((a,b)=>Number(a)-Number(b)).forEach(u=>{
      const fontes=ugMap[u],tu=soma(fontes);
      const nome=fontes[0]&&fontes[0].NOUG&&fontes[0].NOUG!=='Sem nome'?fontes[0].NOUG:'';
      const uid=gid+'_u'+u;
      html+='<tr class="row-ug" data-par="'+gid+'" style="display:none" onclick="toggle(\''+uid+'\')">'
           +'<td><span class="tog" id="tog_'+uid+'">▶</span> UG '+u+(nome?' · '+esc(nome):'')
           +' <span style="font-size:10px;opacity:.5">('+fontes.length+' fonte'+(fontes.length!==1?'s':'')+')</span></td>'
           +'<td></td><td></td>'+valCols(tu)+'</tr>';

      fontes.forEach(r=>{
        const nf=r.NOFONTE&&r.NOFONTE!=='Sem nome'?r.NOFONTE:'';
        html+='<tr class="row-fonte" data-par="'+uid+'" style="display:none">'
             +'<td>Fonte '+r.COFONTE+'</td>'
             +'<td style="text-align:left;color:var(--muted);overflow:hidden;text-overflow:ellipsis" title="'+esc(nf)+'">'+esc(nf)+'</td>'
             +'<td style="text-align:left;color:var(--text)">'+mesTxt+'</td>'
             +valCols(r)+'</tr>';
      });
    });
  });
  tb.innerHTML=html;

  const tot=soma(fil);
  tf.innerHTML='<td>Total Geral · '+nG+' Gestão/ões · '+nU+' UGs · '+nF.toLocaleString('pt-BR')+' fontes</td>'
    +'<td></td><td></td>'
    +'<td class="'+vc(tot.SALDO)+'">'+num(tot.SALDO)+'</td>';
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
      if(t.textContent.trim()==='▼')t.textContent='▶';
      fecharDescendentes(t.id.replace('tog_',''));
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
  const pos=fil.filter(r=>r.SALDO>=0.005),neg=fil.filter(r=>r.SALDO<=-0.005);
  const sPos=pos.reduce((a,r)=>a+r.SALDO,0),sNeg=neg.reduce((a,r)=>a+r.SALDO,0);
  document.getElementById('krow').innerHTML=
    '<div class="kpi"><div class="kl">Saldos positivos</div><div class="kv vp">'+brl(sPos)+'</div><div class="ks">'+pos.length.toLocaleString('pt-BR')+' UG × fonte</div></div>'
   +'<div class="kpi '+(neg.length?'ka':'')+'"><div class="kl">Saldos negativos</div><div class="kv '+(neg.length?'vn':'vz')+'">'+brl(sNeg)+'</div><div class="ks">'+neg.length.toLocaleString('pt-BR')+' UG × fonte — passível de conferência</div></div>'
   +'<div class="kpi ko"><div class="kl">Disponibilidade Real</div><div class="kv '+vc(sm('SALDO'))+'">'+brl(sm('SALDO'))+'</div><div class="ks">Conta 721190200 · fontes selecionadas</div></div>';
}

/* ── Exportar CSV ── */
function exportar(){
  if(!fil.length)return alert('Nenhum dado para exportar.');
  const mes=mesSel!==''?nomeMes(mesSel):'Todos';
  const cols=['COGESTAO','NOGESTAO','COUG','NOUG','COFONTE','NOFONTE'];
  const hdrs=['Gestao','Nome Gestao','Unidade Gestora','Nome UG','Fonte','Nome Fonte','Mes','Disponibilidade Real (R$)'];
  const cel=v=>{if(typeof v==='number')return String(v).replace('.',',');const s=String(v??'');return /^\d+$/.test(s)?'="'+s+'"':s.replace(/;/g,',');};
  const linhas=[hdrs.join(';')].concat(fil.map(r=>cols.map(c=>cel(r[c])).concat([mes,cel(r.SALDO)]).join(';')));
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob(['\ufeff'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),download:'superavit_a_devolver.csv'});
  a.click();URL.revokeObjectURL(a.href);
}
</script>
</body>
</html>
"""

# ── Helpers ────────────────────────────────────────────────────────────────────
def _normalizar(df: pd.DataFrame) -> list[dict]:
    out = []
    for r in df.to_dict(orient="records"):
        out.append({
            "COGESTAO": str(int(r["COGESTAO"])),
            "NOGESTAO": str(r["NOGESTAO"]).strip(),
            "COUG":     str(int(r["COUG"])),
            "NOUG":     str(r["NOUG"]).strip(),
            "COFONTE":  str(int(r["COFONTE"])),
            "NOFONTE":  str(r["NOFONTE"]).strip(),
            "SALDO":    float(r["SALDO"] or 0),
        })
    return out


def _gzip_b64(data) -> str:
    j = json.dumps(data, ensure_ascii=False)
    return base64.b64encode(gzip.compress(j.encode("utf-8"), compresslevel=9)).decode("ascii")


def gerar_html(dados_por_mes: dict) -> str:
    dados_b64 = {mes: _gzip_b64(_normalizar(df)) for mes, df in dados_por_mes.items()}
    ugs_txt = "; ".join(f"{u} / {g} ({n})" for u, g, n in UGS_EXCECAO)
    html = HTML_TEMPLATE
    html = html.replace("{dados_b64}", json.dumps(dados_b64, ensure_ascii=False))
    html = html.replace("{timestamp}", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    html = html.replace("{fontes_exc_txt}", ", ".join(str(f) for f in FONTES_EXCECAO))
    html = html.replace("{prefixos_exc_txt}", ", ".join(PREFIXOS_FONTE_EXCECAO))
    html = html.replace("{ugs_exc_txt}", ugs_txt + " — UG / Gestão (nome)")
    return html


# ── Publicação no GitHub ────────────────────────────────────────────────────────
def publicar_github(caminho: str, mensagem_commit: str) -> None:
    if os.environ.get("NO_GIT_PUSH"):
        return
    pasta = Path(__file__).parent
    url_remote = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"

    def git(*args):
        r = subprocess.run(["git", "-C", str(pasta)] + list(args), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or r.stdout.strip())
        return r.stdout.strip()

    print(f"[{datetime.now():%H:%M:%S}] Publicando no GitHub...")
    try:
        git("remote", "set-url", "origin", url_remote)
    except RuntimeError:
        git("remote", "add", "origin", url_remote)

    git("add", caminho)
    try:
        git("commit", "-m", mensagem_commit)
    except RuntimeError as e:
        if "nothing to commit" not in str(e):
            raise
    git("pull", "--rebase", "--autostash", "origin", GITHUB_BRANCH)
    git("push", "origin", GITHUB_BRANCH)
    print(f"[{datetime.now():%H:%M:%S}] Publicado com sucesso.")
    print(f"  -> https://{GITHUB_USER}.github.io/{GITHUB_REPO}/{caminho}")


# ── Main ────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Superávit a Devolver — conta 721190200 por Gestão/UG/Fonte.")
    parser.add_argument("--ug", type=str, default=None, help="Filtrar por Unidade Gestora")
    parser.add_argument("--out", type=str, default=ARQUIVO_HTML, help="Arquivo HTML de saída")
    parser.add_argument("--no-push", action="store_true", default=False, help="Gera HTML sem publicar no GitHub")
    args = parser.parse_args()

    oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")

    filtro_ug = "AND s.COUG = :ug" if args.ug else ""
    params = {"ug": int(args.ug)} if args.ug else None

    def run(conn, filtro_mes: str = "") -> pd.DataFrame:
        sql = (SQL
               .replace("{schema}", SCHEMA)
               .replace("{conta}", str(CONTA))
               .replace("{digitos}", ", ".join(f"'{d}'" for d in PRIMEIRO_DIGITO_FONTE))
               .replace("{fontes_exc}", ", ".join(str(f) for f in FONTES_EXCECAO))
               .replace("{prefixos_exc}", ", ".join(f"'{p}'" for p in PREFIXOS_FONTE_EXCECAO))
               .replace("{ugs_exc}", ", ".join(str(u) for u in UGS_EXCECAO_COD))
               .replace("{filtro_ug}", filtro_ug)
               .replace("{filtro_mes}", filtro_mes))
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            cols = [c[0] for c in cur.description]
            return pd.DataFrame(cur.fetchall(), columns=cols)

    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle...")
    dados_por_mes: dict[str, pd.DataFrame] = {}
    try:
        with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
            print(f"[{datetime.now():%H:%M:%S}] Todos os meses...")
            df_todos = run(conn)
            dados_por_mes[""] = df_todos
            print(f"  {len(df_todos):,} registros.")

            for mes in range(15):
                print(f"[{datetime.now():%H:%M:%S}] Mês {mes}...", end=" ")
                df_mes = run(conn, f"AND s.INMES <= {mes}")
                if not df_mes.empty:
                    dados_por_mes[str(mes)] = df_mes
                    print(f"{len(df_mes):,} registros.")
                else:
                    print("sem dados.")
    except oracledb.DatabaseError as e:
        print(f"\nErro de banco de dados: {e}", file=sys.stderr)
        sys.exit(1)

    html = gerar_html(dados_por_mes)
    out = Path(args.out)
    out.write_text(html, encoding="utf-8")
    print(f"[{datetime.now():%H:%M:%S}] HTML salvo: {out} ({out.stat().st_size / 1_048_576:.1f} MB)")

    if not args.no_push:
        publicar_github(
            caminho=ARQUIVO_HTML,
            mensagem_commit=f"chore: atualiza superavit a devolver -- {datetime.now():%d/%m/%Y %H:%M}",
        )

    brl = lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    print("\n-- Resumo (Todos os meses) ----------------------------------")
    print(f"  Registros (UG x Fonte) : {len(df_todos):,}")
    print(f"  UGs                    : {df_todos['COUG'].nunique():,}")
    print(f"  Fontes                 : {df_todos['COFONTE'].nunique():,}")
    print(f"  Saldo total (D-C)      : {brl(float(df_todos['SALDO'].sum()))}")
    print("-------------------------------------------------------------")


if __name__ == "__main__":
    main()
