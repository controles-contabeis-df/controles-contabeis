"""
Correspondência Patrimonial Ativo x Passivo — por Saldo
Gera painel HTML autocontido e publica no GitHub Pages.

Monitora equações entre contas do Ativo (classe 1) e do Passivo (classe 2),
consultando VSALDOCONTABIL. O painel agrupa por UG + mês para verificar se os
saldos intragovernamentais se compensam na consolidação do GDF.

Equações monitoradas (extensíveis em EQUACOES):
  EQ1: 21142XXXX = 113620101 + 113620103
  EQ2: 218820101 = 113620102 + 113620104
  EQ3: 218820104 = 112120101
  EQ4: 218820107 = 112120104
  EQ5: 218820108 = 112120107
  EQ6: 218924019 = 112322200

Uso:
    python extrair_correspondencia_ativo_passivo_saldo.py
    python extrair_correspondencia_ativo_passivo_saldo.py --no-push
"""
import argparse, base64, gzip, json, os, subprocess, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime
from pathlib import Path

import oracledb
import pandas as pd
from dotenv import load_dotenv

PASTA       = Path(__file__).parent
load_dotenv(PASTA / ".env")

ORACLE_USER  = os.environ["ORACLE_USER"]
ORACLE_PASS  = os.environ["ORACLE_PASS"]
ORACLE_DSN   = os.environ["ORACLE_DSN"]
SCHEMA       = "MIL2026."
ARQUIVO_HTML = "correspondencia_ativo_passivo_saldo.html"

INSTANT_CLIENT_DIR = r"C:\oracle\instantclient_23_0"

MESES = {1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
         7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}

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
    {"id": "EQ5", "desc": "218820108 = 112120107",
     "passivo_exact": [218820108], "passivo_prefix": None,
     "ativo": [112120107]},
    {"id": "EQ6", "desc": "218924019 = 112322200",
     "passivo_exact": [218924019], "passivo_prefix": None,
     "ativo": [112322200]},
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
        lo = int(pfx) * 10**(9 - len(pfx))
        hi = (int(pfx) + 1) * 10**(9 - len(pfx)) - 1
        parts.append(f"v.COCONTACONTABIL BETWEEN {lo} AND {hi}")
    return " OR ".join(parts)

# ── SQL ───────────────────────────────────────────────────────────────────────
# Saldo líquido por UG + conta + mês.
# Classe 1 (ativo/devedora):  saldo = VADEBITO - VACREDITO
# Classe 2 (passivo/credora): saldo = VACREDITO - VADEBITO
SQL = f"""
SELECT
  v.INMES,
  v.COGESTAO,
  v.COUG,
  TO_CHAR(v.COCONTACONTABIL)                         AS COCONTACONTABIL,
  ROUND(SUM(
    CASE SUBSTR(TO_CHAR(v.COCONTACONTABIL), 1, 1)
      WHEN '1' THEN v.VADEBITO - v.VACREDITO
      WHEN '2' THEN v.VACREDITO - v.VADEBITO
      ELSE 0
    END
  ), 2)                                              AS VLSALDO
FROM {SCHEMA}VSALDOCONTABIL v
WHERE ({_where_contas()})
GROUP BY v.INMES, v.COGESTAO, v.COUG, TO_CHAR(v.COCONTACONTABIL)
ORDER BY v.INMES, v.COUG, TO_CHAR(v.COCONTACONTABIL)
"""

SQL_UG = f"SELECT TO_CHAR(COUG) AS COUG, TRIM(NOUG) AS NOUG FROM {SCHEMA}VUNIDADEGESTORA"

SQL_GESTAO = f"SELECT TO_CHAR(COGESTAO) AS COGESTAO, TRIM(NOGESTAO) AS NOGESTAO FROM {SCHEMA}GESTAO"

SQL_CONTA = f"""SELECT TO_CHAR(COCONTACONTABIL) AS COCONTACONTABIL, TRIM(NOCONTACONTABIL) AS NOCONTACONTABIL
FROM {SCHEMA}VCONTACONTABIL
WHERE COCONTACONTABIL IN ({",".join(str(c) for c in sorted(_CONTAS_ATIVO + _CONTAS_EXATAS))})"""

# ── HTML Template ─────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<!-- v-corresp-saldo-1 -->
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Correspondência Ativo x Passivo — por Saldo — SIGGO</title>
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
.eq-btns{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.eq-btns label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px;margin-right:2px}
.btn-eq{border:1.5px solid;border-radius:20px;padding:4px 12px;font-size:11px;font-weight:600;cursor:pointer;background:transparent;transition:background .15s,color .15s}
.btn-eq.active{color:#fff}
.btn-eq1{border-color:#c0392b;color:#c0392b}   .btn-eq1.active{background:#c0392b}
.btn-eq2{border-color:#b7860b;color:#b7860b}   .btn-eq2.active{background:#b7860b}
.btn-eq3{border-color:#1a7a44;color:#1a7a44}   .btn-eq3.active{background:#1a7a44}
.btn-eq4{border-color:#6c3483;color:#6c3483}   .btn-eq4.active{background:#6c3483}
.btn-eq5{border-color:#b84b00;color:#b84b00}   .btn-eq5.active{background:#b84b00}
.btn-eq6{border-color:#007b7b;color:#007b7b}   .btn-eq6.active{background:#007b7b}
.btn-lim{border:1.5px solid var(--border);border-radius:20px;padding:4px 12px;font-size:11px;cursor:pointer;background:transparent;color:var(--muted)}
.btn-lim:hover{border-color:var(--teal);color:var(--teal)}
.kpi-row{display:flex;flex-wrap:wrap;gap:10px;padding:14px 28px;background:var(--bg)}
.kpi{background:var(--surface);border:1.5px solid var(--border);border-radius:var(--radius);padding:10px 16px;min-width:160px;box-shadow:var(--shadow);transition:border-color .2s}
.kpi .kt{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}
.kpi .kv{font-size:15px;font-weight:700}
.kpi.ka{border-color:#c0392b}.kpi.ka .kv{color:#c0392b}
.kpi.km{border-color:#b7860b}.kpi.km .kv{color:#b7860b}
.kpi.kg{border-color:#1a7a44}.kpi.kg .kv{color:#1a7a44}
.kpi.kv4{border-color:#6c3483}.kpi.kv4 .kv{color:#6c3483}
.kpi.kv5{border-color:#b84b00}.kpi.kv5 .kv{color:#b84b00}
.kpi.kv6{border-color:#007b7b}.kpi.kv6 .kv{color:#007b7b}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);margin:0 28px 28px;overflow:hidden}
.card-hd{display:flex;align-items:center;justify-content:space-between;padding:12px 18px;border-bottom:1px solid var(--border);background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff}
.card-hd h2{font-size:12px;font-weight:700;letter-spacing:.5px;text-transform:uppercase}
#ttitle{font-size:11px;color:#9ab0cc}
.pg-row{display:flex;gap:8px;align-items:center}
.pg-btn{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.25);color:#fff;border-radius:6px;padding:4px 12px;font-size:11px;cursor:pointer}
.pg-btn:hover{background:rgba(255,255,255,.22)}
#pg-info{font-size:11px;color:#9ab0cc}
.tbl-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{position:sticky;top:58px;background:var(--navy);color:#fff;padding:9px 10px;font-size:11px;font-weight:600;letter-spacing:.3px;text-transform:uppercase;white-space:nowrap;cursor:pointer;user-select:none;z-index:10}
th:hover{background:var(--navy-light)}
.si{font-size:10px;opacity:.65;margin-left:3px}
td{padding:7px 10px;border-bottom:1px solid var(--border);vertical-align:middle}
tr.row-ug{cursor:pointer}
tr.row-ug:hover td{background:var(--hover)}
tr.dr{background:var(--row-alt);display:none}
tr.dr.alt{background:var(--bg)}
tr.dr.open{display:table-row}
.left{text-align:left}
.vp{color:var(--red);font-weight:600;text-align:right}
.vz{color:var(--muted);text-align:right}
.vn{color:var(--green);text-align:right}
td:not(.left){text-align:right}
tfoot td{font-weight:700;background:var(--navy);color:#fff;padding:8px 10px;border:none;text-align:right}
tfoot td.left{text-align:left}
.tog{font-size:9px;opacity:.45;margin-right:4px;display:inline-block;transition:transform .15s}
.tog.open{transform:rotate(90deg)}
#loading{display:none;position:fixed;inset:0;background:rgba(13,27,62,.72);z-index:9999;align-items:center;justify-content:center;flex-direction:column;gap:12px}
#loading.show{display:flex}
.spin{width:38px;height:38px;border:4px solid rgba(255,255,255,.2);border-top-color:var(--teal-light);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
#loading p{color:#fff;font-size:13px}
.csv-btn{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.25);color:#fff;border-radius:6px;padding:4px 12px;font-size:11px;cursor:pointer;margin-left:8px}
.csv-btn:hover{background:rgba(255,255,255,.2)}
</style>
</head>
<body>
<div id="loading"><div class="spin"></div><p>Descomprimindo dados&hellip;</p></div>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">&#9878;</div>
    <h1>Correspondência Ativo x Passivo<span>Por Saldo — VSALDOCONTABIL &middot; SIGGO &middot; Exercício 2026</span></h1>
  </div>
  <div style="display:flex;align-items:center;gap:16px">
    <button class="csv-btn" onclick="exportCSV()">&#8595; CSV</button>
    <span id="ts">__TIMESTAMP__</span>
  </div>
</header>

<div class="fbar">
  <div class="fg">
    <label>Saldo at&#233; o m&#234;s</label>
    <select id="fmes" onchange="aplicar()">
      <option value="">Ano completo</option>
    </select>
  </div>
  <div class="fg">
    <label>Gestão</label>
    <select id="fgest" onchange="aplicar()">
      <option value="">Todas as gestões</option>
    </select>
  </div>
  <div class="fg">
    <label>UG</label>
    <div class="ac-wrap" style="width:300px">
      <input id="ac-ug" class="ac-input" placeholder="Buscar UG..." autocomplete="off"
             oninput="onAC()" onfocus="onAC()" onblur="setTimeout(()=>closeAC(),180)">
      <button class="ac-clear" id="ac-clr" onclick="clearAC()">&#10005;</button>
      <div class="ac-dd" id="ac-dd"></div>
    </div>
  </div>
  <div class="fg">
    <label>Filtrar por equação</label>
    <div class="eq-btns">
      <button id="btn-eq1" class="btn-eq btn-eq1" onclick="filtrarEq('eq1')">&#9651; Diverg. EQ1</button>
      <button id="btn-eq2" class="btn-eq btn-eq2" onclick="filtrarEq('eq2')">&#9651; Diverg. EQ2</button>
      <button id="btn-eq3" class="btn-eq btn-eq3" onclick="filtrarEq('eq3')">&#9651; Diverg. EQ3</button>
      <button id="btn-eq4" class="btn-eq btn-eq4" onclick="filtrarEq('eq4')">&#9651; Diverg. EQ4</button>
      <button id="btn-eq5" class="btn-eq btn-eq5" onclick="filtrarEq('eq5')">&#9651; Diverg. EQ5</button>
      <button id="btn-eq6" class="btn-eq btn-eq6" onclick="filtrarEq('eq6')">&#9651; Diverg. EQ6</button>
      <button class="btn-lim" onclick="limpar()">Limpar</button>
    </div>
  </div>
</div>

<div class="kpi-row">
  <div class="kpi" id="kpi-eq1"><div class="kt">EQ1 — 21142XXXX = 113620101+103</div><div class="kv" id="kv-eq1">—</div></div>
  <div class="kpi" id="kpi-eq2"><div class="kt">EQ2 — 218820101 = 113620102+104</div><div class="kv" id="kv-eq2">—</div></div>
  <div class="kpi" id="kpi-eq3"><div class="kt">EQ3 — 218820104 = 112120101</div><div class="kv" id="kv-eq3">—</div></div>
  <div class="kpi" id="kpi-eq4"><div class="kt">EQ4 — 218820107 = 112120104</div><div class="kv" id="kv-eq4">—</div></div>
  <div class="kpi" id="kpi-eq5"><div class="kt">EQ5 — 218820108 = 112120107</div><div class="kv" id="kv-eq5">—</div></div>
  <div class="kpi" id="kpi-eq6"><div class="kt">EQ6 — 218924019 = 112322200</div><div class="kv" id="kv-eq6">—</div></div>
</div>

<div class="card">
  <div class="card-hd">
    <h2>Saldos por UG &middot; Mês</h2>
    <div style="display:flex;align-items:center;gap:16px">
      <span id="ttitle">—</span>
      <div class="pg-row">
        <button class="pg-btn" onclick="mudarPg(-1)">&#8592;</button>
        <span id="pg-info">—</span>
        <button class="pg-btn" onclick="mudarPg(1)">&#8594;</button>
      </div>
    </div>
  </div>
  <div class="tbl-wrap">
    <table>
      <thead><tr>
        <th style="width:28px"></th>
        <th class="left" style="min-width:220px" onclick="sortBy('UG')">UG <span id="s_UG" class="si">&#8645;</span></th>
        <th style="width:110px" onclick="sortBy('ATIVO')">Ativo <span id="s_ATIVO" class="si">&#8645;</span></th>
        <th style="width:110px" onclick="sortBy('PASSIVO')">Passivo <span id="s_PASSIVO" class="si">&#8645;</span></th>
        <th style="width:200px" onclick="sortBy('DIV')">Diverg&#234;ncia <span id="s_DIV" class="si">&#8645;</span></th>
      </tr></thead>
      <tbody id="tb"></tbody>
      <tfoot><tr id="tfoot-row"><td class="left" colspan="5">&#8212;</td></tr></tfoot>
    </table>
  </div>
</div>

<script>
const EQUACOES=__EQUACOES__;
const UG_NAMES=__UG_NAMES__;
const GESTAO_NAMES=__GESTAO_NAMES__;
const CONTA_NAMES=__CONTA_NAMES__;
const MESES_PT={1:'Jan',2:'Fev',3:'Mar',4:'Abr',5:'Mai',6:'Jun',7:'Jul',8:'Ago',9:'Set',10:'Out',11:'Nov',12:'Dez'};

function decomp(b64){
  const bin=atob(b64),buf=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++)buf[i]=bin.charCodeAt(i);
  const ds=new DecompressionStream('gzip');
  const w=ds.writable.getWriter();w.write(buf);w.close();
  return new Response(ds.readable).text();
}

decomp('__DADOS__').then(txt=>{
  const p=JSON.parse(txt);
  const cols=p.cols;
  window.ALL=p.rows.map(r=>Object.fromEntries(cols.map((k,i)=>[k,r[i]])));
  init();
});

function n(v){return v==null||v===''?0:+v||0;}
function brl(v){return new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(v);}
function vc(v){return Math.abs(v)<0.005?'vz':v<0?'vn':'vp';}
function fmtG(g){return g==null?'':String(g).padStart(5,'0');}

function isAtivo(acct){return String(acct).charAt(0)==='1';}
function isPassivo(acct){return String(acct).charAt(0)==='2';}
function prefixMatch(acct,pfx){return pfx&&String(acct).startsWith(pfx);}
function passivoEq(eq,byAcct){
  let s=0;
  if(eq.passivo_prefix){for(const[k,v]of Object.entries(byAcct)){if(prefixMatch(k,eq.passivo_prefix))s+=v;}}
  if(eq.passivo_exact){for(const c of eq.passivo_exact)s+=byAcct[String(c)]||0;}
  return s;
}

const PG_SZ=50;
let pg=1,sortCol='UG',sortDir=1,filterMode='',ugSel='',mesSel='',gestSel='';
let filDocs=[];

function init(){
  document.getElementById('loading').classList.remove('show');
  const meses=[...new Set(window.ALL.map(r=>n(r.INMES)))].sort((a,b)=>a-b);
  const fm=document.getElementById('fmes');
  meses.forEach(m=>{const o=document.createElement('option');o.value=m;o.textContent='Até '+(MESES_PT[m]||m);fm.appendChild(o);});
  const gestoes=[...new Set(window.ALL.map(r=>String(r.COGESTAO||'')))].sort();
  const fg=document.getElementById('fgest');
  gestoes.forEach(g=>{const o=document.createElement('option');o.value=g;o.textContent=fmtG(g)+(GESTAO_NAMES[g]?' — '+GESTAO_NAMES[g]:'');fg.appendChild(o);});
  aplicar();
}

function ugLabel(coug,cogest){const nm=UG_NAMES[String(coug)];return fmtG(cogest)+' \u00b7 '+String(coug)+(nm?' \u2014 '+nm:'');}

let acData=[];
function onAC(){
  const v=document.getElementById('ac-ug').value.trim().toLowerCase();
  document.getElementById('ac-clr').style.display=v?'block':'none';
  if(!v){closeAC();ugSel='';aplicar();return;}
  const hits=acData.filter(x=>x.label.toLowerCase().includes(v)).slice(0,40);
  const dd=document.getElementById('ac-dd');
  if(!hits.length){dd.innerHTML='<div class="ac-dd-empty">Nenhuma UG encontrada.</div>';dd.style.display='block';return;}
  dd.innerHTML=hits.map(x=>`<div class="ac-dd-item" onmousedown="pickAC('${x.id}','${x.label.replace(/'/g,"\\'")}')"><strong>${x.id}</strong> — ${x.name||''}</div>`).join('');
  dd.style.display='block';
}
function pickAC(id,lbl){document.getElementById('ac-ug').value=lbl;document.getElementById('ac-clr').style.display='block';document.getElementById('ac-dd').style.display='none';ugSel=id;aplicar();}
function closeAC(){document.getElementById('ac-dd').style.display='none';}
function clearAC(){document.getElementById('ac-ug').value='';document.getElementById('ac-clr').style.display='none';ugSel='';aplicar();}

function aplicar(){
  mesSel=document.getElementById('fmes').value;
  gestSel=document.getElementById('fgest').value;
  const data=window.ALL.filter(r=>{
    if(mesSel&&n(r.INMES)>Number(mesSel))return false;
    if(gestSel&&String(r.COGESTAO||'')!==gestSel)return false;
    return true;
  });
  const ugIds=[...new Set(data.map(r=>String(r.COUG)))].sort();
  acData=ugIds.map(id=>({id,name:UG_NAMES[id]||'',label:id+(UG_NAMES[id]?' — '+UG_NAMES[id]:'')}));

  const map={};
  data.forEach(r=>{
    const key=String(r.COGESTAO)+'|'+String(r.COUG);
    if(!map[key])map[key]={key,COGESTAO:r.COGESTAO,COUG:r.COUG,
                           totAtivo:0,totPassivo:0,divEQ1:0,divEQ2:0,divEQ3:0,divEQ4:0,divEQ5:0,divEQ6:0,maxDiv:0,byAcct:{},rows:[]};
    const d=map[key];
    const vl=n(r.VLSALDO);
    const acct=String(r.COCONTACONTABIL);
    d.byAcct[acct]=(d.byAcct[acct]||0)+vl;
    d.rows.push(r);
    if(isAtivo(acct))d.totAtivo+=vl;
    else if(isPassivo(acct))d.totPassivo+=vl;
  });
  Object.values(map).forEach(d=>{
    d.totAtivo=Math.round(d.totAtivo*100)/100;
    d.totPassivo=Math.round(d.totPassivo*100)/100;
    EQUACOES.forEach(eq=>{
      const pas=Math.round(passivoEq(eq,d.byAcct)*100)/100;
      const ati=Math.round(eq.ativo.reduce((s,c)=>s+(d.byAcct[String(c)]||0),0)*100)/100;
      const div=Math.round((pas-ati)*100)/100;
      if(eq.id==='EQ1')d.divEQ1=div;
      if(eq.id==='EQ2')d.divEQ2=div;
      if(eq.id==='EQ3')d.divEQ3=div;
      if(eq.id==='EQ4')d.divEQ4=div;
      if(eq.id==='EQ5')d.divEQ5=div;
      if(eq.id==='EQ6')d.divEQ6=div;
    });
    d.maxDiv=[d.divEQ1,d.divEQ2,d.divEQ3,d.divEQ4,d.divEQ5,d.divEQ6].reduce((m,v)=>Math.abs(v)>Math.abs(m)?v:m,0);
  });

  filDocs=Object.values(map).filter(d=>{
    if(ugSel&&String(d.COUG)!==ugSel)return false;
    if(filterMode==='eq1'&&Math.abs(d.divEQ1)<0.005)return false;
    if(filterMode==='eq2'&&Math.abs(d.divEQ2)<0.005)return false;
    if(filterMode==='eq3'&&Math.abs(d.divEQ3)<0.005)return false;
    if(filterMode==='eq4'&&Math.abs(d.divEQ4)<0.005)return false;
    if(filterMode==='eq5'&&Math.abs(d.divEQ5)<0.005)return false;
    if(filterMode==='eq6'&&Math.abs(d.divEQ6)<0.005)return false;
    return true;
  });
  filDocs.sort(cmpDocs);pg=1;render();kpis();
}

const SORT_COLS=['UG','ATIVO','PASSIVO','DIV'];
let sortInit=false;
function sortBy(col){
  if(sortCol===col)sortDir*=-1;else{sortCol=col;sortDir=1;}
  SORT_COLS.forEach(c=>{const el=document.getElementById('s_'+c);if(el)el.textContent=c===sortCol?(sortDir>0?'\u2191':'\u2193'):'\u21c5';});
  filDocs.sort(cmpDocs);pg=1;render();
}
function cmpDocs(a,b){
  if(sortCol==='UG')    return sortDir*(String(a.COUG).localeCompare(String(b.COUG)));
  if(sortCol==='MES')   return sortDir*(a.INMES-b.INMES);
  if(sortCol==='ATIVO') return sortDir*(n(a.totAtivo)-n(b.totAtivo));
  if(sortCol==='PASSIVO')return sortDir*(n(a.totPassivo)-n(b.totPassivo));
  if(sortCol==='DIV')   return sortDir*(Math.abs(n(a.maxDiv))-Math.abs(n(b.maxDiv)));
  return 0;
}

function toggleDoc(did){
  const tog=document.getElementById('tog_'+did);
  if(tog)tog.classList.toggle('open');
  document.querySelectorAll('[data-doc="'+did+'"]').forEach(r=>r.classList.toggle('open'));
}

function render(){
  const tb=document.getElementById('tb');
  const tf=document.getElementById('tfoot-row');
  const start=(pg-1)*PG_SZ,end=Math.min(start+PG_SZ,filDocs.length);
  let html='';
  filDocs.slice(start,end).forEach(d=>{
    const did='d'+String(d.key).replace(/[^a-z0-9]/gi,'_');
    const totA=n(d.totAtivo),totP=n(d.totPassivo);
    const _dp=[];
    if(Math.abs(n(d.divEQ1))>=0.005)_dp.push('EQ1 '+brl(n(d.divEQ1)));
    if(Math.abs(n(d.divEQ2))>=0.005)_dp.push('EQ2 '+brl(n(d.divEQ2)));
    if(Math.abs(n(d.divEQ3))>=0.005)_dp.push('EQ3 '+brl(n(d.divEQ3)));
    if(Math.abs(n(d.divEQ4))>=0.005)_dp.push('EQ4 '+brl(n(d.divEQ4)));
    if(Math.abs(n(d.divEQ5))>=0.005)_dp.push('EQ5 '+brl(n(d.divEQ5)));
    if(Math.abs(n(d.divEQ6))>=0.005)_dp.push('EQ6 '+brl(n(d.divEQ6)));
    const divStr=_dp.length?_dp.join(' / '):'\u2014';
    const hasdiv=_dp.length>0;
    html+='<tr class="row-ug" onclick="toggleDoc(\''+did+'\')">'
      +'<td><span class="tog" id="tog_'+did+'">&#9654;</span></td>'
      +'<td class="left">'+ugLabel(d.COUG,d.COGESTAO)+'</td>'
      +'<td class="'+vc(totA)+'">'+brl(totA)+'</td>'
      +'<td class="'+vc(totP)+'">'+brl(totP)+'</td>'
      +'<td class="'+(hasdiv?'vp':'vz')+'" style="font-size:11px;white-space:nowrap">'+divStr+'</td>'
      +'</tr>';
    // detalhe: agrupa por conta, soma meses
    const byAcctRows={};
    d.rows.forEach(r=>{const a=String(r.COCONTACONTABIL);byAcctRows[a]=(byAcctRows[a]||0)+n(r.VLSALDO);});
    const acctsSorted=Object.keys(byAcctRows).sort();
    acctsSorted.forEach((acct,j)=>{
      const vl=byAcctRows[acct];
      const contaNome=CONTA_NAMES[acct]?' <span style="font-size:10px;opacity:.65">'+CONTA_NAMES[acct]+'</span>':'';
      html+='<tr class="dr'+(j%2?' alt':'')+'" data-doc="'+did+'">'
        +'<td></td>'
        +'<td class="left" style="font-size:11px;padding-left:24px">'
        +'<code style="font-size:11px;color:var(--navy)">'+acct+'</code>'+contaNome+'</td>'
        +'<td class="'+(isAtivo(acct)?(Math.abs(vl)<0.005?'vz':vc(vl)):'vz')+'">'+(isAtivo(acct)&&Math.abs(vl)>=0.005?brl(vl):'\u2014')+'</td>'
        +'<td class="'+(isPassivo(acct)?(Math.abs(vl)<0.005?'vz':vc(vl)):'vz')+'">'+(isPassivo(acct)&&Math.abs(vl)>=0.005?brl(vl):'\u2014')+'</td>'
        +'<td class="vz">\u2014</td>'
        +'</tr>';
    });
  });
  tb.innerHTML=html||'<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted)">Nenhuma UG encontrada.</td></tr>';
  const totA=filDocs.reduce((s,d)=>s+n(d.totAtivo),0);
  const totP=filDocs.reduce((s,d)=>s+n(d.totPassivo),0);
  const nDiv=filDocs.filter(d=>Math.abs(n(d.maxDiv))>=0.005).length;
  tf.innerHTML='<td class="left">Total \u00b7 '+filDocs.length.toLocaleString('pt-BR')+' UG(s)</td>'
    +'<td class="'+vc(totA)+'">'+brl(totA)+'</td>'
    +'<td class="'+vc(totP)+'">'+brl(totP)+'</td>'
    +'<td class="'+(nDiv>0?'vp':'vz')+'" style="font-size:11px">'+(nDiv>0?nDiv.toLocaleString('pt-BR')+' com diverg.':'\u2014')+'</td>';
  document.getElementById('ttitle').textContent=filDocs.length.toLocaleString('pt-BR')+' UG(s)';
  paginar();
}

function kpis(){
  const totD1=filDocs.reduce((s,d)=>s+n(d.divEQ1),0);
  const totD2=filDocs.reduce((s,d)=>s+n(d.divEQ2),0);
  const totD3=filDocs.reduce((s,d)=>s+n(d.divEQ3),0);
  const totD4=filDocs.reduce((s,d)=>s+n(d.divEQ4),0);
  const totD5=filDocs.reduce((s,d)=>s+n(d.divEQ5),0);
  const totD6=filDocs.reduce((s,d)=>s+n(d.divEQ6),0);
  document.getElementById('kv-eq1').innerHTML='<span class="'+vc(totD1)+'">'+brl(totD1)+'</span>';
  document.getElementById('kv-eq2').innerHTML='<span class="'+vc(totD2)+'">'+brl(totD2)+'</span>';
  document.getElementById('kv-eq3').innerHTML='<span class="'+vc(totD3)+'">'+brl(totD3)+'</span>';
  document.getElementById('kv-eq4').innerHTML='<span class="'+vc(totD4)+'">'+brl(totD4)+'</span>';
  document.getElementById('kv-eq5').innerHTML='<span class="'+vc(totD5)+'">'+brl(totD5)+'</span>';
  document.getElementById('kv-eq6').innerHTML='<span class="'+vc(totD6)+'">'+brl(totD6)+'</span>';
  document.getElementById('kpi-eq1').className='kpi'+(Math.abs(totD1)>0.005?' ka':'');
  document.getElementById('kpi-eq2').className='kpi'+(Math.abs(totD2)>0.005?' km':'');
  document.getElementById('kpi-eq3').className='kpi'+(Math.abs(totD3)>0.005?' kg':'');
  document.getElementById('kpi-eq4').className='kpi'+(Math.abs(totD4)>0.005?' kv4':'');
  document.getElementById('kpi-eq5').className='kpi'+(Math.abs(totD5)>0.005?' kv5':'');
  document.getElementById('kpi-eq6').className='kpi'+(Math.abs(totD6)>0.005?' kv6':'');
}

function paginar(){
  const tot=filDocs.length,pages=Math.max(1,Math.ceil(tot/PG_SZ));
  document.getElementById('pg-info').textContent='Pág. '+pg+' / '+pages;
}
function mudarPg(d){
  const pages=Math.max(1,Math.ceil(filDocs.length/PG_SZ));
  pg=Math.max(1,Math.min(pages,pg+d));render();
}

function filtrarEq(eq){
  filterMode=filterMode===eq?'':eq;
  ['eq1','eq2','eq3','eq4','eq5','eq6'].forEach(e=>{
    document.getElementById('btn-'+e).classList.toggle('active',filterMode===e);
  });
  aplicar();
}
function limpar(){
  filterMode='';ugSel='';
  document.getElementById('ac-ug').value='';
  document.getElementById('ac-clr').style.display='none';
  ['eq1','eq2','eq3','eq4','eq5','eq6'].forEach(e=>document.getElementById('btn-'+e).classList.remove('active'));
  aplicar();
}

function exportCSV(){
  const mesRef=mesSel?'Até '+(MESES_PT[Number(mesSel)]||mesSel):'Ano completo';
  const hdr=['Gestao','COUG','Nome UG','Periodo','Ativo','Passivo','Divergencia'];
  const rows=filDocs.map(d=>[
    fmtG(d.COGESTAO),String(d.COUG),UG_NAMES[String(d.COUG)]||'',
    mesRef,
    n(d.totAtivo).toFixed(2),n(d.totPassivo).toFixed(2),
    n(d.maxDiv).toFixed(2)
  ]);
  const csv=[hdr,...rows].map(r=>r.map(v=>'"'+String(v).replace(/"/g,'""')+'"').join(';')).join('\r\n');
  const a=document.createElement('a');
  a.href='data:text/csv;charset=utf-8,\uFEFF'+encodeURIComponent(csv);
  a.download='correspondencia_saldo.csv';a.click();
}

document.getElementById('loading').classList.add('show');
</script>
</body>
</html>
"""

# ── extrair ───────────────────────────────────────────────────────────────────
def extrair():
    oracledb.init_oracle_client(lib_dir=INSTANT_CLIENT_DIR)
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        df       = pd.read_sql(SQL,        conn)
        df_ug    = pd.read_sql(SQL_UG,     conn)
        df_gest  = pd.read_sql(SQL_GESTAO, conn)
        df_conta = pd.read_sql(SQL_CONTA,  conn)
    ug_names     = dict(zip(df_ug["COUG"].astype(str),         df_ug["NOUG"]))
    gestao_names = dict(zip(df_gest["COGESTAO"].astype(str),   df_gest["NOGESTAO"]))
    conta_names  = dict(zip(df_conta["COCONTACONTABIL"].astype(str), df_conta["NOCONTACONTABIL"]))
    return df, ug_names, gestao_names, conta_names

# ── gerar_html ────────────────────────────────────────────────────────────────
def gerar_html(df, ug_names, gestao_names, conta_names):
    cols = list(df.columns)
    rows = []
    for r in df.itertuples(index=False):
        row = []
        for v in r:
            if isinstance(v, float) and pd.isna(v): row.append(None)
            elif hasattr(v, "item"): row.append(v.item())
            else: row.append(v)
        rows.append(row)
    payload   = {"cols": cols, "rows": rows}
    json_str  = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    dados_b64 = base64.b64encode(gzip.compress(json_str.encode("utf-8"), compresslevel=9)).decode()
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

# ── publicar ──────────────────────────────────────────────────────────────────
def publicar(html: str, no_push: bool):
    dest = PASTA / ARQUIVO_HTML
    dest.write_text(html, encoding="utf-8")
    print(f"  HTML salvo: {dest}")
    if no_push:
        print("  Push ignorado (--no-push ou NO_GIT_PUSH=1).")
        return
    cmds = [
        ["git", "-C", str(PASTA), "add", ARQUIVO_HTML],
        ["git", "-C", str(PASTA), "commit", "-m", f"atualiza {ARQUIVO_HTML}"],
        ["git", "-C", str(PASTA), "push"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in r.stdout + r.stderr:
            print(f"  Aviso git: {r.stderr.strip()}")

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()
    no_push = args.no_push or bool(os.environ.get("NO_GIT_PUSH"))

    print("Correspondência Ativo x Passivo — por Saldo...")
    print("[1/3] Conectando ao Oracle...")
    df, ug_names, gestao_names, conta_names = extrair()
    print(f"  {len(df):,} linhas ({df['COUG'].nunique()} UGs, {df['INMES'].nunique()} meses).")
    print("[2/3] Gerando HTML...")
    html = gerar_html(df, ug_names, gestao_names, conta_names)
    j = json.dumps({"cols": list(df.columns), "rows": df.values.tolist()},
                   ensure_ascii=False, separators=(",", ":"))
    raw_kb = len(j.encode()) // 1024
    comp_kb = len(gzip.compress(j.encode(), compresslevel=9)) // 1024
    print(f"  JSON: {raw_kb} KB -> comprimido: {comp_kb} KB")
    print("[3/3] Salvando...")
    publicar(html, no_push)
    print("Concluido.")

if __name__ == "__main__":
    main()
