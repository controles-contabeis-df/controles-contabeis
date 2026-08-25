"""
Correspondência Ativo x Passivo — por Saldo Consolidado
Gera painel HTML autocontido e publica no GitHub Pages.

Visão CONSOLIDADA: agrega os saldos de todas as UGs. Lançamentos
intragovernamentais que são contrapartida de outra UG se anulam no
consolidado, revelando se as equações fecham em nível de GDF.

Equações monitoradas (em sincronia com extrair_correspondencia_ativo_passivo.py):
  EQ1: 21142XXXX = 113620101 + 113620103
  EQ2: 218820101 = 113620102 + 113620104
  EQ3: 218820104 = 112120101
  EQ4: 218820107 = 112120104
  EQ5: 214320100 + 214325100 + 218820108 + 218827005 = 112120107
  EQ6: 218924019 = 112322200
  EQ7: 113220700 + 112920101 = 0

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

MESES = {0:"Saldo Inicial",1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
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
        lo = int(pfx) * 10**(9 - len(pfx))
        hi = (int(pfx) + 1) * 10**(9 - len(pfx)) - 1
        parts.append(f"v.COCONTACONTABIL BETWEEN {lo} AND {hi}")
    return " OR ".join(parts)

# ── SQL ───────────────────────────────────────────────────────────────────────
# Consolidado: sem agrupamento por UG — todos os saldos somados.
# Classe 1 (ativo/devedora):  saldo = VADEBITO - VACREDITO
# Classe 2 (passivo/credora): saldo = VACREDITO - VADEBITO
SQL = f"""
SELECT
  v.INMES,
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
GROUP BY v.INMES, TO_CHAR(v.COCONTACONTABIL)
ORDER BY v.INMES, TO_CHAR(v.COCONTACONTABIL)
"""

def _sql_conta() -> str:
    parts = []
    if _CONTAS_ATIVO + _CONTAS_EXATAS:
        lst = ",".join(str(c) for c in sorted(_CONTAS_ATIVO + _CONTAS_EXATAS))
        parts.append(f"COCONTACONTABIL IN ({lst})")
    for pfx in _PREFIXOS:
        lo = int(pfx) * 10**(9 - len(pfx))
        hi = (int(pfx) + 1) * 10**(9 - len(pfx)) - 1
        parts.append(f"COCONTACONTABIL BETWEEN {lo} AND {hi}")
    return " OR ".join(parts)

SQL_CONTA = f"""SELECT TO_CHAR(COCONTACONTABIL) AS COCONTACONTABIL, TRIM(NOCONTACONTABIL) AS NOCONTACONTABIL
FROM {SCHEMA}VCONTACONTABIL
WHERE {_sql_conta()}"""

# ── HTML Template ─────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<!-- v-corresp-saldo-4 -->
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Correspondência — Ativo × Passivo por Saldo — SIGGO</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --navy:#0d1b3e;--navy-mid:#162550;--navy-light:#1e3267;
  --teal:#0090a8;--teal-light:#00b8d4;
  --surface:#fff;--bg:#f2f5f9;--border:#dce3ed;
  --row-alt:#f7f9fc;--hover:#eaf4f7;
  --text:#1a2033;--muted:#6b7a99;
  --red:#c0392b;--green:#1a7a44;--amber:#b7860b;--radius:10px;
  --shadow:0 2px 12px rgba(13,27,62,.10);
}
body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}
header{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}
.hlogo{width:32px;height:32px;background:var(--teal);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}
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
.ac-wrap{position:relative}
.ac-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text);transition:border-color .15s}
.ac-input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none;line-height:1;padding:2px}
.ac-dd{position:absolute;top:calc(100% + 4px);left:0;right:0;min-width:320px;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:240px;overflow-y:auto;display:none}
.ac-dd-item{padding:8px 12px;cursor:pointer;font-size:12px;border-bottom:1px solid var(--border)}
.ac-dd-item:last-child{border-bottom:none}
.ac-dd-item:hover{background:var(--hover)}
.ac-dd-item strong{color:var(--navy)}
.ac-dd-empty{padding:12px;color:var(--muted);font-size:12px;text-align:center}
.btns{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto;align-items:flex-end}
.btn{border:none;border-radius:6px;padding:8px 14px;font-size:12px;font-weight:600;cursor:pointer;transition:opacity .15s;white-space:nowrap}
.btn:hover{opacity:.82}
.btn-p{background:var(--teal);color:#fff}
.btn-g{background:var(--border);color:var(--text)}
.kpi-section{background:var(--surface);border-bottom:1px solid var(--border)}
.kpi-toggle{display:flex;align-items:center;gap:8px;padding:8px 28px;cursor:pointer;user-select:none;font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.kpi-toggle:hover{background:var(--row-alt)}
.kpi-toggle-arrow{font-size:10px;transition:transform .2s;display:inline-block}
.kpi-toggle-arrow.open{transform:rotate(90deg)}
.kpi-body{display:none;flex-direction:column;gap:8px;padding:10px 28px 14px}
.kpi-body.open{display:flex}
.kpi-row{display:flex;flex-wrap:wrap;gap:8px}
.kpi-group{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;flex:1;min-width:220px}
.kpi-group-title{background:var(--navy);color:#c8d8ec;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;padding:5px 12px}
.kpi-group-inner{display:flex;flex-wrap:wrap}
.kpi{padding:9px 14px;position:relative;overflow:hidden;flex:1;min-width:110px;border-right:1px solid var(--border);background:var(--surface)}
.kpi:last-child{border-right:none}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}
.kpi.kw::before{background:linear-gradient(90deg,var(--amber),#e6b800)}
.kpi.ka::before{background:linear-gradient(90deg,var(--red),#e74c3c)}
.kpi.ko::before{background:linear-gradient(90deg,var(--green),#27ae60)}
.kpi-total-row{display:flex;flex-wrap:wrap;gap:8px}
.kpi-total-row .kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);flex:1;min-width:170px}
.kpi-total-row .kv{font-size:20px}
.kl{font-size:9px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:3px}
.kv{font-size:13px;font-weight:700;font-variant-numeric:tabular-nums}
.ks{font-size:10.5px;color:var(--muted);margin-top:4px}
.badge{display:inline-block;border-radius:10px;padding:1px 8px;font-size:10px;font-weight:700}
.br{background:#fde8e6;color:var(--red)}
.bg{background:#e8f5ee;color:var(--green)}
.vp{color:var(--green)}
.vn{color:var(--red)}
.vz{color:var(--muted)}
.cnt-bar{padding:8px 28px;font-size:12px;color:var(--muted);background:var(--surface);border-bottom:1px solid var(--border)}
.tw{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto;margin:18px 28px}
table{width:100%;border-collapse:collapse;min-width:850px}
thead th{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:right;white-space:nowrap;letter-spacing:.3px}
thead th.left{text-align:left}
tr.row-eq{background:#1e3267;cursor:pointer}
tr.row-eq:hover td{filter:brightness(1.1)}
tr.row-eq td{color:#e8f0fc;font-weight:700;padding:10px 14px;font-size:12px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.row-eq td.left{text-align:left}
tr.row-acct{background:var(--surface)}
tr.row-acct.alt{background:var(--row-alt)}
tr.row-acct:hover td{background:var(--hover)}
tr.row-acct td{padding:8px 14px 8px 40px;font-size:12px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--border);font-variant-numeric:tabular-nums}
tr.row-acct td.left{text-align:left;color:var(--muted)}
tfoot tr td{background:#f0f4fb;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;font-size:12.5px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tfoot tr td.left{text-align:left}
.tog{display:inline-block;width:14px;text-align:center;font-size:10px;margin-right:4px}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">&#9878;</div>
    <h1>Correspond&#234;ncia &#8212; Ativo &#215; Passivo
      <span>Por Saldo Consolidado &#183; SIGGO &#183; Exerc&#237;cio 2026</span>
    </h1>
    <a class="voltar" href="index.html">&#8592; Painel inicial</a>
  </div>
  <span id="ts">__TIMESTAMP__</span>
</header>

<div class="fbar">
  <div class="fg">
    <label>Saldo acumulado at&#233;</label>
    <select id="fm" onchange="aplicar()"></select>
  </div>
  <div class="fg" style="min-width:300px">
    <label>Conta Cont&#225;bil</label>
    <div class="ac-wrap">
      <input id="fc-input" class="ac-input" style="min-width:300px"
        placeholder="C&#243;digo ou nome da conta..."
        autocomplete="off" oninput="onContaInput()" onfocus="onContaInput()">
      <button class="ac-clear" id="fc-clear" onclick="limContaFil()" title="Limpar">&#x2715;</button>
      <div class="ac-dd" id="fc-dd"></div>
    </div>
  </div>
  <div class="fg">
    <label>Exibir equa&#231;&#245;es</label>
    <select id="fs" onchange="aplicar()">
      <option value="todos">Todas</option>
      <option value="com_div">Com diverg&#234;ncia</option>
      <option value="sem_div">Sem diverg&#234;ncia</option>
    </select>
  </div>
  <div class="btns">
    <button class="btn btn-g" onclick="expandirTudo()">&#9660; Expandir</button>
    <button class="btn btn-g" onclick="recolherTudo()">&#9650; Recolher</button>
    <button class="btn btn-g" onclick="limpar()">&#8635; Limpar</button>
    <button class="btn btn-p" onclick="exportar()">&#8595; CSV</button>
  </div>
</div>

<div class="kpi-section">
  <div class="kpi-body open" id="krow-total"></div>
  <div class="kpi-toggle" onclick="toggleKpis(this)">
    <span class="kpi-toggle-arrow" id="kpi-arrow">&#9658;</span>
    Detalhar por equa&#231;&#227;o
  </div>
  <div class="kpi-body" id="krow"></div>
</div>
<div class="cnt-bar"><span id="cnt"></span></div>

<div class="tw">
  <table>
    <thead><tr>
      <th class="left" style="min-width:340px">Equa&#231;&#227;o / Conta Cont&#225;bil</th>
      <th class="left" style="min-width:120px">M&#234;s</th>
      <th style="width:195px">Ativo</th>
      <th style="width:195px">Passivo</th>
      <th style="width:160px">Diverg&#234;ncia</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
    <tfoot><tr id="tfoot"></tr></tfoot>
  </table>
</div>

<script>
const EQUACOES=__EQUACOES__;
const CONTA_NAMES=__CONTA_NAMES__;
const MESES_PT={0:'Saldo Inicial',1:'Janeiro',2:'Fevereiro',3:'Mar\u00e7o',4:'Abril',5:'Maio',6:'Junho',
                7:'Julho',8:'Agosto',9:'Setembro',10:'Outubro',11:'Novembro',12:'Dezembro'};
const EQ_COLORS=['var(--red)','var(--amber)','#2e7d32','#5e35b1','#e65100','#00695c','#880e4f'];

function decomp(b64){
  const bin=atob(b64),buf=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++)buf[i]=bin.charCodeAt(i);
  const ds=new DecompressionStream('gzip');
  const w=ds.writable.getWriter();w.write(buf);w.close();
  return new Response(ds.readable).text();
}
decomp('__DADOS__').then(txt=>{
  const p=JSON.parse(txt);
  window.ALL=p.rows.map(r=>Object.fromEntries(p.cols.map((k,i)=>[k,r[i]])));
  init();
});

function n(v){return v==null||v===''?0:+v||0;}
function brl(v){return new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(v);}
function vc(v){return Math.abs(v)<0.005?'vz':v>0?'vp':'vn';}
function prefixMatch(a,p){return p&&String(a).startsWith(p);}

function passivoEqSum(eq,byAcct){
  let s=0;
  if(eq.passivo_prefix){for(const[k,v]of Object.entries(byAcct)){if(prefixMatch(k,eq.passivo_prefix))s+=v;}}
  if(eq.passivo_exact){for(const c of eq.passivo_exact)s+=byAcct[String(c)]||0;}
  return Math.round(s*100)/100;
}
function ativoEqSum(eq,byAcct){
  return Math.round(eq.ativo.reduce((s,c)=>s+(byAcct[String(c)]||0),0)*100)/100;
}

let contaFil='';
let curData=null;

function init(){
  const meses=[...new Set(window.ALL.map(r=>n(r.INMES)))].sort((a,b)=>a-b);
  const fm=document.getElementById('fm');
  fm.innerHTML='';
  meses.forEach(m=>{
    const o=document.createElement('option');
    o.value=m;
    o.textContent=MESES_PT[m]||('M'+m);
    fm.appendChild(o);
  });
  if(meses.length)fm.value=meses[meses.length-1];
  aplicar();
}

function buildMesData(maxMes){
  const byAcct={};
  window.ALL.filter(r=>n(r.INMES)<=maxMes).forEach(r=>{
    const a=String(r.COCONTACONTABIL);
    byAcct[a]=(byAcct[a]||0)+n(r.VLSALDO);
  });
  const eqData=EQUACOES.map(eq=>{
    const accts=[];
    if(eq.passivo_exact){
      eq.passivo_exact.forEach(c=>{
        const a=String(c),vl=Math.round((byAcct[a]||0)*100)/100;
        accts.push({acct:a,vl,side:'passivo'});
      });
    }
    if(eq.passivo_prefix){
      Object.entries(byAcct).forEach(([a,vl])=>{
        if(prefixMatch(a,eq.passivo_prefix))
          accts.push({acct:a,vl:Math.round(vl*100)/100,side:'passivo'});
      });
    }
    eq.ativo.forEach(c=>{
      const a=String(c),vl=Math.round((byAcct[a]||0)*100)/100;
      accts.push({acct:a,vl,side:'ativo'});
    });
    accts.sort((a,b)=>a.acct.localeCompare(b.acct));
    const passivoSum=passivoEqSum(eq,byAcct);
    const ativoSum=ativoEqSum(eq,byAcct);
    const div=Math.round((passivoSum-ativoSum)*100)/100;
    return{eq,accts,passivoSum,ativoSum,div};
  });
  return{maxMes,eqData};
}

function mesLabel(maxMes){
  const m=Number(maxMes);
  return MESES_PT[m]||('M'+m);
}

function acctMatch(acct){
  if(!contaFil)return true;
  const q=contaFil.toLowerCase();
  return acct.toLowerCase().includes(q)||(CONTA_NAMES[acct]||'').toLowerCase().includes(q);
}

function aplicar(){
  const maxMes=Number(document.getElementById('fm').value);
  const showFil=document.getElementById('fs').value;
  curData=buildMesData(maxMes);
  const period=mesLabel(maxMes);
  const tb=document.getElementById('tbody');
  const tf=document.getElementById('tfoot');
  let html='',totA=0,totP=0,totD=0,nEQ=0;

  curData.eqData.forEach((d,i)=>{
    const matchAccts=contaFil?d.accts.filter(a=>acctMatch(a.acct)):d.accts;
    if(contaFil&&!matchAccts.length)return;
    if(showFil==='com_div'&&Math.abs(d.div)<0.005)return;
    if(showFil==='sem_div'&&Math.abs(d.div)>=0.005)return;

    const eid='eq'+i;
    const col=EQ_COLORS[i%EQ_COLORS.length];
    const dCls=Math.abs(d.div)<0.005?'vz':d.div>0?'vp':'vn';

    html+='<tr class="row-eq" onclick="toggle(\''+eid+'\')">'
      +'<td class="left" style="border-left:3px solid '+col+'">'
      +'<span class="tog" id="tog_'+eid+'" data-open="0">&#9658;</span>'
      +'<strong style="color:'+col+'">'+d.eq.id+'</strong>'
      +' <span style="font-weight:400;font-size:11px;opacity:.8">'+d.eq.desc+'</span></td>'
      +'<td class="left" style="font-size:11.5px;font-weight:400;opacity:.8">'+period+'</td>'
      +'<td class="'+vc(d.ativoSum)+'">'+brl(d.ativoSum)+'</td>'
      +'<td class="'+vc(d.passivoSum)+'">'+brl(d.passivoSum)+'</td>'
      +'<td class="'+dCls+'" style="font-weight:800">'+brl(d.div)+'</td>'
      +'</tr>';

    const accts=contaFil?matchAccts:d.accts;
    accts.forEach((a,j)=>{
      const nome=CONTA_NAMES[a.acct]||'';
      const av=a.side==='ativo'?a.vl:0;
      const pv=a.side==='passivo'?a.vl:0;
      html+='<tr class="row-acct'+(j%2?' alt':'')+'" data-par="'+eid+'" style="display:none">'
        +'<td class="left"><code style="font-size:11.5px;color:var(--navy);font-weight:700">'+a.acct+'</code>'
        +(nome?' <span style="color:var(--muted);font-size:11px">'+nome+'</span>':'')+'</td>'
        +'<td class="left" style="font-size:11px;color:var(--muted)">'+period+'</td>'
        +'<td class="'+(Math.abs(av)>=0.005?vc(av):'vz')+'">'+(Math.abs(av)>=0.005?brl(av):'&#8212;')+'</td>'
        +'<td class="'+(Math.abs(pv)>=0.005?vc(pv):'vz')+'">'+(Math.abs(pv)>=0.005?brl(pv):'&#8212;')+'</td>'
        +'<td class="vz">&#8212;</td>'
        +'</tr>';
    });

    totA+=d.ativoSum;totP+=d.passivoSum;totD+=d.div;nEQ++;
  });

  tb.innerHTML=html||'<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted)">Nenhum registro encontrado.</td></tr>';
  tf.innerHTML='<td class="left">Total Geral &middot; '+nEQ+' equa\u00e7\u00e3o(es)</td>'
    +'<td class="left" style="font-size:11px">'+period+'</td>'
    +'<td class="'+vc(totA)+'">'+brl(totA)+'</td>'
    +'<td class="'+vc(totP)+'">'+brl(totP)+'</td>'
    +'<td class="'+vc(totD)+'" style="font-weight:800">'+brl(totD)+'</td>';
  document.getElementById('cnt').textContent=nEQ+' equa\u00e7\u00e3o(es) exibida(s) \u00b7 '+period;
  kpis(totA,totP,totD);
}

function kpis(totA,totP,totD){
  if(!curData)return;
  const nDiv=curData.eqData.filter(d=>Math.abs(d.div)>=0.005).length;
  const dcT=Math.abs(totD)<0.005?'ko':totD>0?'kw':'ka';
  document.getElementById('krow-total').innerHTML=
    '<div class="kpi-total-row">'
    +'<div class="kpi"><div class="kl">Total Ativo</div><div class="kv '+vc(totA)+'">'+brl(totA)+'</div></div>'
    +'<div class="kpi"><div class="kl">Total Passivo</div><div class="kv '+vc(totP)+'">'+brl(totP)+'</div></div>'
    +'<div class="kpi '+dcT+'"><div class="kl">Diverg\u00eancia Total (P\u2212A)</div>'
    +'<div class="kv '+vc(totD)+'">'+brl(totD)+'</div>'
    +'<div class="ks"><span class="badge '+(nDiv>0?'br':'bg')+'">'+nDiv+' eq. c/ diverg\u00eancia</span></div></div>'
    +'</div>';
  let khtml='<div class="kpi-row">';
  curData.eqData.forEach((d,i)=>{
    const col=EQ_COLORS[i%EQ_COLORS.length];
    const dc=Math.abs(d.div)<0.005?'ko':d.div>0?'kw':'ka';
    khtml+='<div class="kpi-group">'
      +'<div class="kpi-group-title" style="border-left:3px solid '+col+'">'
      +d.eq.id+'<span style="font-weight:400;margin-left:8px;opacity:.65;font-size:8.5px;text-transform:none">'+d.eq.desc+'</span></div>'
      +'<div class="kpi-group-inner">'
      +'<div class="kpi"><div class="kl">Ativo</div><div class="kv '+vc(d.ativoSum)+'">'+brl(d.ativoSum)+'</div></div>'
      +'<div class="kpi"><div class="kl">Passivo</div><div class="kv '+vc(d.passivoSum)+'">'+brl(d.passivoSum)+'</div></div>'
      +'<div class="kpi '+dc+'"><div class="kl">Diverg\u00eancia</div><div class="kv '+vc(d.div)+'">'+brl(d.div)+'</div></div>'
      +'</div></div>';
  });
  khtml+='</div>';
  document.getElementById('krow').innerHTML=khtml;
}

function toggleKpis(el){
  const body=document.getElementById('krow');
  const arrow=document.getElementById('kpi-arrow');
  const open=body.classList.toggle('open');
  arrow.classList.toggle('open',open);
}

function toggle(id){
  const tog=document.getElementById('tog_'+id);
  if(!tog)return;
  const expanded=tog.dataset.open==='1';
  document.querySelectorAll('[data-par="'+id+'"]').forEach(tr=>tr.style.display=expanded?'none':'');
  tog.dataset.open=expanded?'0':'1';
  tog.textContent=expanded?'►':'▼';
}
function expandirTudo(){
  document.querySelectorAll('[data-par]').forEach(tr=>tr.style.display='');
  document.querySelectorAll('.tog').forEach(el=>{el.textContent='▼';el.dataset.open='1';});
}
function recolherTudo(){
  document.querySelectorAll('[data-par]').forEach(tr=>tr.style.display='none');
  document.querySelectorAll('.tog').forEach(el=>{el.textContent='►';el.dataset.open='0';});
}

function onContaInput(){
  const v=document.getElementById('fc-input').value.trim();
  contaFil=v;
  document.getElementById('fc-clear').style.display=v?'block':'none';
  const dd=document.getElementById('fc-dd');
  if(!v){dd.style.display='none';aplicar();return;}
  const q=v.toLowerCase();
  const matches=Object.entries(CONTA_NAMES)
    .filter(([k,nm])=>k.includes(q)||nm.toLowerCase().includes(q)).slice(0,20);
  if(!matches.length){
    dd.innerHTML='<div class="ac-dd-empty">Nenhuma conta encontrada</div>';
    dd.style.display='block';
  } else {
    dd.innerHTML=matches.map(([k,nm])=>'<div class="ac-dd-item" onmousedown="selConta(\''+k+'\')">'
      +'<strong>'+k+'</strong> \u2014 '+nm+'</div>').join('');
    dd.style.display='block';
  }
  aplicar();
}
function selConta(code){
  const nm=CONTA_NAMES[code]||'';
  contaFil=code;
  document.getElementById('fc-input').value=code+(nm?' \u2014 '+nm:'');
  document.getElementById('fc-clear').style.display='block';
  document.getElementById('fc-dd').style.display='none';
  aplicar();
}
function limContaFil(){
  contaFil='';
  document.getElementById('fc-input').value='';
  document.getElementById('fc-clear').style.display='none';
  document.getElementById('fc-dd').style.display='none';
  aplicar();
}
document.addEventListener('click',e=>{
  if(!e.target.closest('.ac-wrap'))document.getElementById('fc-dd').style.display='none';
});

function limpar(){
  const meses=[...new Set(window.ALL.map(r=>n(r.INMES)))].sort((a,b)=>a-b);
  document.getElementById('fm').value=meses[meses.length-1]||'';
  document.getElementById('fs').value='todos';
  limContaFil();
}

function exportar(){
  if(!curData)return;
  const period=mesLabel(document.getElementById('fm').value);
  const hdr=['Equacao','Descricao','Conta','Nome Conta','Tipo','Periodo','Ativo','Passivo','Divergencia'];
  const rows=[];
  curData.eqData.forEach(d=>{
    rows.push([d.eq.id,d.eq.desc,'','','',period,d.ativoSum.toFixed(2),d.passivoSum.toFixed(2),d.div.toFixed(2)]);
    d.accts.forEach(a=>{
      const av=a.side==='ativo'?a.vl:0;
      const pv=a.side==='passivo'?a.vl:0;
      rows.push([d.eq.id,'',a.acct,CONTA_NAMES[a.acct]||'',a.side,period,av.toFixed(2),pv.toFixed(2),'']);
    });
  });
  const csv=[hdr,...rows].map(r=>r.map(v=>'"'+String(v).replace(/"/g,'""')+'"').join(';')).join('\r\n');
  const a=document.createElement('a');
  a.href='data:text/csv;charset=utf-8,\uFEFF'+encodeURIComponent(csv);
  a.download='correspondencia_saldo_consolidado.csv';a.click();
}
</script>
</body>
</html>
"""

# ── extrair ───────────────────────────────────────────────────────────────────
def extrair():
    oracledb.init_oracle_client(lib_dir=INSTANT_CLIENT_DIR)
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        df       = pd.read_sql(SQL,       conn)
        df_conta = pd.read_sql(SQL_CONTA, conn)
    conta_names = dict(zip(df_conta["COCONTACONTABIL"].astype(str), df_conta["NOCONTACONTABIL"]))
    return df, conta_names

# ── gerar_html ────────────────────────────────────────────────────────────────
def gerar_html(df, conta_names):
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
    eq_js     = json.dumps(EQUACOES,    ensure_ascii=False)
    conta_js  = json.dumps(conta_names, ensure_ascii=False)
    ts        = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    html = HTML_TEMPLATE
    html = html.replace("__EQUACOES__",    eq_js)
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
        ["git", "-C", str(PASTA), "commit", "-m", f"auto: atualiza {ARQUIVO_HTML}"],
        ["git", "-C", str(PASTA), "push"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 and "nothing to commit" not in r.stdout + r.stderr:
            print(f"  Aviso git: {r.stderr.strip()}")
        elif r.returncode == 0 and cmd[3] == "push":
            print(f"  Publicado: https://controles-contabeis-df.github.io/controles-contabeis/{ARQUIVO_HTML}")

# ── main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()
    no_push = args.no_push or bool(os.environ.get("NO_GIT_PUSH"))

    print(f"[{datetime.now():%H:%M:%S}] Correspondência Ativo x Passivo — por Saldo Consolidado…")
    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle…")
    df, conta_names = extrair()
    print(f"  {len(df):,} linhas ({df['INMES'].nunique()} meses).")
    print(f"[{datetime.now():%H:%M:%S}] Gerando HTML…")
    html = gerar_html(df, conta_names)
    j = json.dumps({"cols": list(df.columns), "rows": df.values.tolist()},
                   ensure_ascii=False, separators=(",", ":"))
    raw_kb  = len(j.encode()) // 1024
    comp_kb = len(gzip.compress(j.encode(), compresslevel=9)) // 1024
    print(f"  JSON: {raw_kb} KB → comprimido: {comp_kb} KB")
    publicar(html, no_push)
    print(f"[{datetime.now():%H:%M:%S}] Concluído.")

if __name__ == "__main__":
    main()
