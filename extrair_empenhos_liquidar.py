"""
Repasse a Receber — Despesas Empenhadas e não Liquidadas — SIGGO/Oracle
Extrai saldos da conta 622920701 por UG, Empenho, Categoria e Fonte,
gera HTML autocontido com visão hierárquica e publica no GitHub Pages.

Dependências:
    pip install oracledb pandas

Uso:
    python extrair_empenhos_liquidar.py
    python extrair_empenhos_liquidar.py --ug 10101
    python extrair_empenhos_liquidar.py --no-push
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import oracledb
import pandas as pd

# -- Conexão Oracle -------------------------------------------------------------
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ORACLE_USER = os.environ["ORACLE_USER"]
ORACLE_PASS = os.environ["ORACLE_PASS"]
ORACLE_DSN  = os.environ["ORACLE_DSN"]
SCHEMA      = "MIL2026."

# -- GitHub --------------------------------------------------------------------
GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
GITHUB_USER   = os.environ["GITHUB_USER"]
GITHUB_REPO   = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = os.environ["GITHUB_BRANCH"]

ARQUIVO_HTML = "empenhos_liquidar.html"

ANO_EXERCICIO = int(SCHEMA.rstrip(".").replace("MIL", ""))

# -- SQL -----------------------------------------------------------------------
SQL = """
SELECT
    {ano}                                                         AS EXERCICIO,
    sc.COUG,
    NVL(ug.COUG, sc.COUG) || ' - ' || NVL(ug.NOUG, 'Sem nome') AS UNIDADE_GESTORA,
    sc.COGESTAO,
    SUBSTR(sc.COCONTACORRENTE, 1, 11)                            AS EMPENHO,
    sc.INMES,
    sc.INCATEGORIA,
    sc.COFONTE,
    SUM(sc.VACREDITO - sc.VADEBITO)                              AS SALDO
FROM {schema}VSALDOCONTABIL sc
LEFT JOIN {schema}UNIDADEGESTORA ug
       ON ug.COUG = sc.COUG
      AND ug.COUG <> '0'
      AND ug.NOUG NOT LIKE '%TESTE%'
INNER JOIN {schema}FONTERECURSO fr
       ON TO_NUMBER(fr.COFONTE) = TO_NUMBER(sc.COFONTE)
      AND fr.INTIPOFONTE IN (1, 3)
      AND fr.INFONTETESOURO = 'S'
WHERE sc.COCONTACONTABIL = 622920701
{{filtro_ug}}
GROUP BY
    sc.COUG,
    NVL(ug.COUG, sc.COUG) || ' - ' || NVL(ug.NOUG, 'Sem nome'),
    sc.COGESTAO,
    SUBSTR(sc.COCONTACORRENTE, 1, 11),
    sc.INMES,
    sc.INCATEGORIA,
    sc.COFONTE
HAVING SUM(sc.VACREDITO - sc.VADEBITO) > 0
ORDER BY sc.COUG, sc.INMES, sc.COFONTE, sc.INCATEGORIA
""".format(ano=ANO_EXERCICIO, schema=SCHEMA)

# -- HTML template (r-string para preservar \n literal como escape JS) ----------
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Repasse a Receber — Empenhos não Liquidados</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --navy:#0d1b3e;--navy-mid:#162550;--navy-light:#1e3267;
  --teal:#2d9048;--teal-light:#3aaf5c;
  --surface:#ffffff;--bg:#f6fbf6;--border:#c8deca;
  --row-alt:#edf7ed;--hover:#e4f2e3;
  --text:#1a2033;--muted:#6b7a99;
  --red:#c0392b;--green:#1a7a44;--blue-val:#1a4d2e;--radius:10px;
  --shadow:0 2px 12px rgba(13,27,62,.10);
}
body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}
header{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}
.hlogo{width:32px;height:32px;background:#0090a8;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}
header h1{font-size:14px;font-weight:700;letter-spacing:.6px;text-transform:uppercase}
header h1 span{font-weight:400;color:#9ab0cc;font-size:12px;display:block;text-transform:none;letter-spacing:0;margin-top:1px}
#ts{font-size:11px;color:#7a99bb;white-space:nowrap}
.voltar{font-size:11px;color:#7a99bb;text-decoration:none;display:flex;align-items:center;gap:4px;margin-left:20px;opacity:.8}
.voltar:hover{opacity:1}
.aviso{background:#fff8e6;border-bottom:2px solid #f0a500;padding:10px 28px;font-size:12px;color:#7a4a00}
.fbar{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end}
.fg{display:flex;flex-direction:column;gap:4px}
.fg label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.fg select{border:1.5px solid var(--border);border-radius:6px;padding:7px 28px 7px 10px;font-size:12.5px;min-width:160px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E") no-repeat right 9px center;color:var(--text);cursor:pointer;appearance:none}
.fg select:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ug-wrap{position:relative;min-width:260px}
.ug-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text);transition:border-color .15s}
.ug-input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ug-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;line-height:1;display:none}
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
.krow{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;padding:18px 28px 4px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}
.kpi.ka::before{background:linear-gradient(90deg,var(--teal),var(--teal-light))}
.kl{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.kv{font-size:17px;font-weight:700;letter-spacing:-.3px;line-height:1}
.ks{font-size:11px;color:var(--muted);margin-top:5px}
.tsec{padding:16px 28px 32px}
.thead-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}
.ttitle{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.sw{position:relative}
.sw input{border:2px solid var(--teal);border-radius:6px;padding:8px 12px 8px 34px;font-size:13px;width:260px;background:#fff;box-shadow:0 0 0 3px rgba(0,144,168,.08)}
.sw input:focus{outline:none;border-color:var(--navy)}
.sw input::placeholder{color:var(--teal);font-weight:500}
.sw::before{content:'🔍';position:absolute;left:9px;top:50%;transform:translateY(-50%);font-size:13px;pointer-events:none}
.tw{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}
table{width:100%;border-collapse:collapse;min-width:480px}
thead th{background:#495249;color:#e8f0e8;padding:11px 14px;font-size:11px;font-weight:600;white-space:nowrap;letter-spacing:.3px;position:sticky;top:0}
thead th.right{text-align:right}
/* ── Linhas hierárquicas ── */
tr.row-ug{background:#bfd1be;cursor:pointer}
tr.row-ug:hover{background:#b0c5af}
tr.row-ug td{color:#1a4d2e;font-weight:700;font-size:12.5px;padding:10px 14px;border-bottom:2px solid #b0d4bc}
tr.row-ug td.right{color:#1a4d2e;font-size:13px}
tr.row-fonte{background:#e4f2e3;cursor:pointer}
tr.row-fonte:hover{background:#dbeeda}
tr.row-fonte td{color:#1a4d2e;font-weight:600;font-size:12px;padding:8px 14px;border-bottom:1px solid #c8deca}
tr.row-fonte td.right{color:#1a4d2e;font-weight:700}
tr.row-cat{background:#ffffff}
tr.row-cat:nth-child(even){background:#f6fbf6}
tr.row-cat:hover{background:var(--hover)}
tr.row-cat td{font-size:12px;padding:7px 14px;border-bottom:1px solid var(--border)}
tr.row-cat td.right{color:var(--blue-val);font-weight:600}
td.right{text-align:right;font-variant-numeric:tabular-nums}
.ico{display:inline-block;width:16px;font-size:11px;color:var(--teal-light);flex-shrink:0;user-select:none}
.ind1{display:inline-block;width:20px}
.ind2{display:inline-block;width:40px}
tfoot td{background:#dbeeda;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;font-size:12.5px;color:#1a4d2e}
tfoot td.right{color:var(--blue-val);font-weight:700}
.empty{text-align:center;padding:56px;color:var(--muted)}
.vp{color:var(--blue-val);font-weight:700}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">📋</div>
    <h1>Repasse a Receber — Despesas Empenhadas e não Liquidadas
      <span>SIGGO · Ano Exercício 2026 · Conta 622920701</span>
    </h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <span id="ts">Gerado em: {timestamp}</span>
</header>
<div class="aviso">⚠️ <strong>Atenção:</strong> Exibe apenas empenhos com saldo positivo (isto é, saldo &gt; 0). Use para identificar repasses pendentes de registro de Saldo de Repasse a Liberar e Direito a Receber (RPNP).</div>
<div class="fbar">
  <div class="fg">
    <label>Unidade Gestora</label>
    <div class="ug-wrap">
      <input id="fu-input" class="ug-input" type="text" placeholder="Código ou nome…" autocomplete="off"
             oninput="onUGInput()" onfocus="onUGFocus()" onblur="onUGBlur()">
      <button class="ug-clear" id="fu-clear" onclick="limparUG()" title="Limpar">✕</button>
      <div class="ug-dd" id="fu-dd"></div>
    </div>
  </div>
  <div class="fg"><label>Mês</label><select id="fm"><option value="">Todos</option></select></div>
  <div class="fg"><label>Fonte</label><select id="ff"><option value="">Todas</option></select></div>
  <div class="fg"><label>Categoria</label><select id="fcat"><option value="">Todas</option></select></div>
  <div class="bgrp">
    <button class="btn btn-g" onclick="recolherTudo()">⊕ Recolher tudo</button>
    <button class="btn btn-g" onclick="expandirTudo()">⊖ Expandir tudo</button>
    <button class="btn btn-g" onclick="limpar()">↺ Limpar filtros</button>
    <button class="btn btn-p" onclick="exportar()">⬇ Exportar CSV</button>
  </div>
</div>
<div class="krow" id="krow"></div>
<div class="tsec">
  <div class="thead-row">
    <span class="ttitle" id="cnt"></span>
  </div>
  <div class="tw">
    <table>
      <thead>
        <tr>
          <th>UG / Fonte / Categoria</th>
          <th class="right">Saldo (R$)</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
      <tfoot id="tfoot"></tfoot>
    </table>
  </div>
</div>
<script>
const ALL={dados};
let fil=[],ugSel='';

const ugMap={};
ALL.forEach(r=>{if(r.COUG&&!ugMap[r.COUG])ugMap[r.COUG]=r.UNIDADE_GESTORA;});
const ugList=Object.entries(ugMap).map(([c,label])=>({c,label})).sort((a,b)=>a.c.localeCompare(b.c));

function onUGInput(){
  const v=document.getElementById('fu-input').value.toLowerCase();
  const m=v?ugList.filter(u=>u.c.includes(v)||u.label.toLowerCase().includes(v)):ugList;
  renderDD(m);
  if(!v){ugSel='';document.getElementById('fu-clear').style.display='none';}
}
function onUGFocus(){
  const v=document.getElementById('fu-input').value.toLowerCase();
  renderDD(v?ugList.filter(u=>u.c.includes(v)||u.label.toLowerCase().includes(v)):ugList);
}
function onUGBlur(){setTimeout(()=>document.getElementById('fu-dd').style.display='none',200);}
function renderDD(lista){
  const dd=document.getElementById('fu-dd');
  if(!lista.length){dd.innerHTML='<div class="ug-dd-empty">Nenhuma UG encontrada</div>';dd.style.display='block';return;}
  dd.innerHTML=lista.slice(0,80).map(u=>'<div class="ug-dd-item" onmousedown="selUG(\''+u.c+'\',\''+u.label.replace(/\\/g,'\\\\').replace(/'/g,"\\'")+'\')"><strong>'+u.c+'</strong> — '+u.label+'</div>').join('');
  dd.style.display='block';
}
function selUG(c,label){ugSel=c;document.getElementById('fu-input').value=label;document.getElementById('fu-dd').style.display='none';document.getElementById('fu-clear').style.display='block';aplicar();}
function limparUG(){ugSel='';document.getElementById('fu-input').value='';document.getElementById('fu-clear').style.display='none';aplicar();}

const MESES=['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
function init(){
  const mesesDisp=[...new Set(ALL.map(r=>r.INMES))].filter(Boolean).sort((a,b)=>Number(a)-Number(b));
  const fms=document.getElementById('fm');
  fms.innerHTML='<option value="">Todos</option>';
  mesesDisp.forEach(m=>{const o=document.createElement('option');o.value=m;o.textContent=(MESES[Number(m)-1]||m)+' ('+m+')';fms.appendChild(o);});
  fillSel('ff',[...new Set(ALL.map(r=>r.COFONTE))].sort());
  fillSel('fcat',[...new Set(ALL.map(r=>r.INCATEGORIA))].sort());
  aplicar();
}
function fillSel(id,vals){
  const s=document.getElementById(id),p=s.value;
  s.innerHTML='<option value="">Todos</option>';
  vals.forEach(v=>{const o=document.createElement('option');o.value=o.textContent=v;s.appendChild(o)});
  if(p)s.value=p;
}
function aplicar(){
  const fm=document.getElementById('fm').value;
  const ff=document.getElementById('ff').value;
  const fcat=document.getElementById('fcat').value;
  fil=ALL.filter(r=>{
    if(ugSel&&r.COUG!==ugSel)return false;
    if(fm&&String(r.INMES)!==fm)return false;
    if(ff&&String(r.COFONTE)!==ff)return false;
    if(fcat&&String(r.INCATEGORIA)!==fcat)return false;
    return true;
  });
  render();
  kpis();
}
function limpar(){
  ['fm','ff','fcat'].forEach(id=>document.getElementById(id).value='');
  limparUG();
}

function buildTree(data){
  const tree={};
  data.forEach(r=>{
    if(!tree[r.COUG])tree[r.COUG]={nome:r.UNIDADE_GESTORA,total:0,fontes:{}};
    const ug=tree[r.COUG];
    ug.total+=r.SALDO;
    if(!ug.fontes[r.COFONTE])ug.fontes[r.COFONTE]={total:0,cats:{}};
    const fonte=ug.fontes[r.COFONTE];
    fonte.total+=r.SALDO;
    if(!fonte.cats[r.INCATEGORIA])fonte.cats[r.INCATEGORIA]=0;
    fonte.cats[r.INCATEGORIA]+=r.SALDO;
  });
  return tree;
}

const brl=v=>isNaN(v)?'—':Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});

function render(){
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  if(!fil.length){
    tb.innerHTML='<tr><td colspan="2" class="empty">Nenhum registro com os filtros selecionados.</td></tr>';
    tf.innerHTML='';
    document.getElementById('cnt').textContent='0 registros';
    document.getElementById('krow').innerHTML='';
    return;
  }
  const tree=buildTree(fil);
  const ugKeys=Object.keys(tree).sort();
  let html='';
  ugKeys.forEach(ugKey=>{
    const ug=tree[ugKey];
    const uid='u'+ugKey;
    html+='<tr class="row-ug" onclick="toggle(\''+uid+'\')">'
         +'<td><span class="ico" id="i'+uid+'">⊖</span> '+ug.nome+'</td>'
         +'<td class="right">'+brl(ug.total)+'</td>'
         +'</tr>';
    const fonteKeys=Object.keys(ug.fontes).sort();
    fonteKeys.forEach(fKey=>{
      const fonte=ug.fontes[fKey];
      const fid=uid+'f'+fKey;
      html+='<tr class="row-fonte child-of-'+uid+'" onclick="toggle(\''+fid+'\')">'
           +'<td><span class="ind1"></span><span class="ico" id="i'+fid+'">⊖</span> Fonte '+fKey+'</td>'
           +'<td class="right">'+brl(fonte.total)+'</td>'
           +'</tr>';
      const catKeys=Object.keys(fonte.cats).sort();
      catKeys.forEach(cKey=>{
        html+='<tr class="row-cat child-of-'+fid+'">'
             +'<td><span class="ind2"></span> Categoria '+cKey+'</td>'
             +'<td class="right">'+brl(fonte.cats[cKey])+'</td>'
             +'</tr>';
      });
    });
  });
  tb.innerHTML=html;
  const st=fil.reduce((a,r)=>a+r.SALDO,0);
  tf.innerHTML='<tr><td><strong>Total Geral ('+ugKeys.length+' UGs)</strong></td><td class="right vp">'+brl(st)+'</td></tr>';
  const nUgs=ugKeys.length;
  const nRec=fil.length;
  document.getElementById('cnt').textContent=nUgs+' unidade'+(nUgs!==1?'s gestoras':' gestora')+' · '+nRec.toLocaleString('pt-BR')+' empenho'+(nRec!==1?'s':'');
}

function toggle(id){
  const ico=document.getElementById('i'+id);
  if(!ico)return;
  const exp=ico.textContent==='⊖';
  ico.textContent=exp?'⊕':'⊖';
  document.querySelectorAll('.child-of-'+id).forEach(el=>{
    el.style.display=exp?'none':'';
    if(exp){
      const subId=el.className.match(/child-of-(\S+)/);
      const myId=el.getAttribute('onclick');
      if(myId){
        const m=myId.match(/toggle\('([^']+)'\)/);
        if(m){
          const ico2=document.getElementById('i'+m[1]);
          if(ico2)ico2.textContent='⊕';
          document.querySelectorAll('.child-of-'+m[1]).forEach(g=>g.style.display='none');
        }
      }
    }
  });
}
function recolherTudo(){
  document.querySelectorAll('tr.row-ug,tr.row-fonte').forEach(row=>{
    const m=row.getAttribute('onclick');
    if(m){const id=m.match(/toggle\('([^']+)'\)/);if(id){const ico=document.getElementById('i'+id[1]);if(ico&&ico.textContent==='⊖')toggle(id[1]);}}
  });
}
function expandirTudo(){
  document.querySelectorAll('tr.row-ug,tr.row-fonte').forEach(row=>{
    const m=row.getAttribute('onclick');
    if(m){const id=m.match(/toggle\('([^']+)'\)/);if(id){const ico=document.getElementById('i'+id[1]);if(ico&&ico.textContent==='⊕')toggle(id[1]);}}
  });
}

function kpis(){
  const st=fil.reduce((a,r)=>a+r.SALDO,0);
  document.getElementById('krow').innerHTML=fil.length
    ?'<div class="kpi ka"><div class="kl">Saldo de Repasse a Receber de Despesas Empenhadas e não Liquidadas</div><div class="kv vp">'+brl(st)+'</div><div class="ks">Conta 622920701</div></div>'
    :'';
}

function exportar(){
  if(!fil.length)return alert('Nenhum dado para exportar.');
  const cols=['COUG','UNIDADE_GESTORA','COGESTAO','EMPENHO','INMES','INCATEGORIA','COFONTE','SALDO'];
  const cel=v=>{if(typeof v==='number')return String(v).replace('.',',');const s=v??'';return /^\d+$/.test(s)?`="${s}"`:s;};
  const linhas=[cols.join(';'),...fil.map(r=>cols.map(c=>cel(r[c])).join(';'))];
  const a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{type:'text/csv;charset=utf-8'})),download:'empenhos_liquidar.csv'});
  a.click();URL.revokeObjectURL(a.href);
}
['fm','ff','fcat'].forEach(id=>document.getElementById(id).addEventListener('change',aplicar));
init();
</script>
</body>
</html>"""


# -- Oracle --------------------------------------------------------------------
oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")


def extrair(ug: str | None) -> pd.DataFrame:
    filtro_ug = "AND sc.COUG = :ug" if ug else ""
    sql = SQL.format(filtro_ug=filtro_ug)
    params = {"ug": ug} if ug else {}

    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle...")
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        print(f"[{datetime.now():%H:%M:%S}] Executando consulta...")
        with conn.cursor() as cur:
            cur.execute(sql, params)
            colunas = [c[0] for c in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=colunas)

    df["SALDO"] = pd.to_numeric(df["SALDO"], errors="coerce").fillna(0)
    df["EXERCICIO"] = df["EXERCICIO"].astype(int)
    df["COUG"] = df["COUG"].astype(str)
    df["COGESTAO"] = df["COGESTAO"].astype(str)
    df["EMPENHO"] = df["EMPENHO"].astype(str)
    df["INMES"] = df["INMES"].astype(str)
    df["INCATEGORIA"] = df["INCATEGORIA"].astype(str)
    df["COFONTE"] = df["COFONTE"].astype(str)

    print(f"[{datetime.now():%H:%M:%S}] {len(df):,} registros retornados.")
    return df


def gerar_html(df: pd.DataFrame) -> str:
    registros = df.to_dict(orient="records")
    for r in registros:
        for k, v in r.items():
            if hasattr(v, "item"):
                r[k] = v.item()
            elif not isinstance(v, (str, int, float, bool, type(None))):
                r[k] = str(v)

    return (HTML_TEMPLATE
            .replace('{dados}', json.dumps(registros, ensure_ascii=False))
            .replace('{timestamp}', datetime.now().strftime("%d/%m/%Y %H:%M:%S")))


def publicar_github(caminho: str, mensagem_commit: str) -> None:
    if os.environ.get('NO_GIT_PUSH'):
        return
    pasta = Path(__file__).parent
    url_remote = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"

    def git(*args):
        r = subprocess.run(["git", "-C", str(pasta)] + list(args), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return r.stdout.strip()

    print(f"[{datetime.now():%H:%M:%S}] Publicando no GitHub...")
    try:
        git("remote", "set-url", "origin", url_remote)
    except RuntimeError:
        git("remote", "add", "origin", url_remote)

    git("add", caminho)
    git("commit", "-m", mensagem_commit)
    git("pull", "--rebase", "--autostash", "origin", GITHUB_BRANCH)
    git("push", "origin", GITHUB_BRANCH)

    print(f"[{datetime.now():%H:%M:%S}] Publicado com sucesso.")
    print(f"  -> https://{GITHUB_USER}.github.io/{GITHUB_REPO}/{caminho}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Repasse a Receber -- extrai do Oracle e publica no GitHub Pages.")
    parser.add_argument("--ug",      type=str, default=None,         help="Filtrar por Unidade Gestora")
    parser.add_argument("--out",     type=str, default=ARQUIVO_HTML, help="Arquivo HTML de saida")
    parser.add_argument("--no-push", action="store_true",            help="Gera HTML sem publicar no GitHub")
    args = parser.parse_args()

    try:
        df = extrair(args.ug)
    except oracledb.DatabaseError as e:
        print(f"\nErro de banco de dados: {e}", file=sys.stderr)
        sys.exit(1)

    html = gerar_html(df)
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"[{datetime.now():%H:%M:%S}] HTML salvo: {args.out}")

    if not args.no_push:
        ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        publicar_github(
            caminho=ARQUIVO_HTML,
            mensagem_commit=f"chore: atualiza empenhos a liquidar -- {ts}",
        )

    brl = lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    print(f"\nTotal de registros : {len(df):,}")
    print(f"Saldo total        : {brl(float(df['SALDO'].sum()))}\n")


if __name__ == "__main__":
    main()
