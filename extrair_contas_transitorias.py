"""
Contas Transitórias — SIGGO/Oracle
Extrai saldos das contas transitórias, gera HTML autocontido e publica no GitHub Pages.

Dependências:
    pip install oracledb pandas requests

Uso:
    python extrair_contas_transitorias.py
    python extrair_contas_transitorias.py --ug 10101
    python extrair_contas_transitorias.py --no-push   (gera HTML sem publicar)
    python extrair_contas_transitorias.py --ano 2025  (exercício específico)
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import oracledb
import pandas as pd

# ── Conexão Oracle ──────────────────────────────────────────────────────────────
ORACLE_USER = "usefp79"
ORACLE_PASS = "bo39ra"
ORACLE_DSN  = "10.69.1.118:1521/oraprd06"
SCHEMA      = "MIL2026."

# ── GitHub ──────────────────────────────────────────────────────────────────────
GITHUB_TOKEN  = "COLOQUE_SEU_TOKEN_AQUI"   # nunca versionar o token real
GITHUB_USER   = "controles-contabeis-df"
GITHUB_REPO   = "controles-contabeis"
GITHUB_BRANCH = "main"

ARQUIVO_HTML = "contas_transitorias.html"

# ── Contas transitórias monitoradas ────────────────────────────────────────────
CONTAS_TRANSITORIAS = (
    113810604, 113810699,
    113819101, 113819103,
    113829101, 113829102, 113829103,
    218815001, 218815002, 218815003, 218815004, 218815005,
    218815008, 218815009, 218815010,
)

CONTAS_STR = ", ".join(str(c) for c in CONTAS_TRANSITORIAS)

# ── SQL ─────────────────────────────────────────────────────────────────────────
SQL = f"""
SELECT
    sc.ANO                                          AS EXERCICIO,
    sc.INMES                                        AS MES,
    sc.COUG,
    NVL(ug.COUG, sc.COUG) || ' - ' || NVL(ug.NOUG, 'Sem nome')
                                                    AS UNIDADE_GESTORA,
    NVL(ta.COTIPO || ' - ' || ta.NOAGREGACAO, 'Sem classificação')
                                                    AS TIPO_AGREGACAO,
    sc.COCONTACONTABIL                              AS CONTA_CONTABIL,
    SUM(sc.VACREDITO - sc.VADEBITO)                 AS SALDO
FROM {SCHEMA}VSALDOCONTABIL sc
LEFT JOIN {SCHEMA}UNIDADEGESTORA ug
    ON  ug.COUG = sc.COUG
    AND ug.COUG <> '0'
    AND ug.NOUG NOT LIKE '%TESTE%'
LEFT JOIN {SCHEMA}GESTAO g
    ON  g.COGESTAO = sc.COGESTAO
LEFT JOIN {SCHEMA}TIPOAGREGACAOADM taa
    ON  taa.INTIPOADM = g.INTIPOADM
LEFT JOIN {SCHEMA}TIPOAGREGACAO ta
    ON  ta.COTIPO = taa.COTIPO
WHERE sc.COCONTACONTABIL IN ({CONTAS_STR})
{{filtro_ug}}
{{filtro_ano}}
GROUP BY
    sc.ANO,
    sc.INMES,
    sc.COUG,
    NVL(ug.COUG, sc.COUG) || ' - ' || NVL(ug.NOUG, 'Sem nome'),
    NVL(ta.COTIPO || ' - ' || ta.NOAGREGACAO, 'Sem classificação'),
    sc.COCONTACONTABIL
ORDER BY
    sc.ANO, sc.INMES, sc.COUG, sc.COCONTACONTABIL
"""

# ── HTML template ───────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Contas Transitórias — Controle de Saldos</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --navy:#0d1b3e;--navy-mid:#162550;--navy-light:#1e3267;
  --teal:#0090a8;--teal-light:#00b8d4;
  --surface:#fff;--bg:#f2f5f9;--border:#dce3ed;
  --row-alt:#f7f9fc;--hover:#eaf4f7;
  --text:#1a2033;--muted:#6b7a99;
  --red:#c0392b;--green:#1a7a44;--orange:#e67e22;--radius:10px;
  --shadow:0 2px 12px rgba(13,27,62,.10);
}}
body{{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}}
header{{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}}
.hlogo{{width:32px;height:32px;background:var(--teal);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}}
header h1{{font-size:14px;font-weight:700;letter-spacing:.6px;text-transform:uppercase}}
header h1 span{{font-weight:400;color:#9ab0cc;font-size:12px;display:block;text-transform:none;letter-spacing:0;margin-top:1px}}
#ts{{font-size:11px;color:#7a99bb;white-space:nowrap}}
.aviso{{background:#fff8e6;border-bottom:1px solid #f0c060;padding:8px 28px;font-size:11.5px;color:#7a5c00;display:flex;align-items:center;gap:8px}}
.aviso strong{{color:#5a3e00}}
.fbar{{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}}
.fg{{display:flex;flex-direction:column;gap:4px}}
.fg label{{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}}
.fg select{{border:1.5px solid var(--border);border-radius:6px;padding:7px 28px 7px 10px;font-size:12.5px;min-width:140px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E") no-repeat right 9px center;color:var(--text);cursor:pointer;appearance:none}}
.fg select:focus{{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}}
.ug-wrap{{position:relative;min-width:260px}}
.ug-input{{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text);transition:border-color .15s}}
.ug-input:focus{{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}}
.ug-clear{{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}}
.ug-dd{{position:absolute;top:calc(100% + 4px);left:0;right:0;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:240px;overflow-y:auto;display:none}}
.ug-dd-item{{padding:8px 12px;cursor:pointer;font-size:12.5px;border-bottom:1px solid var(--border);transition:background .1s}}
.ug-dd-item:last-child{{border-bottom:none}}
.ug-dd-item:hover{{background:var(--hover)}}
.ug-dd-item strong{{color:var(--navy);font-weight:700}}
.ug-dd-empty{{padding:12px;color:var(--muted);font-size:12px;text-align:center}}
.bgrp{{display:flex;gap:8px;margin-left:auto;align-items:flex-end;flex-wrap:wrap}}
.btn{{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;transition:filter .15s,transform .1s;white-space:nowrap}}
.btn:hover{{filter:brightness(1.08);transform:translateY(-1px)}}
.btn-p{{background:var(--teal);color:#fff}}
.btn-g{{background:var(--border);color:var(--text)}}
.btn-nz{{background:#fde8e6;color:var(--red);border:1.5px solid var(--red)}}
.btn-nz.ativo{{background:var(--red);color:#fff;border-color:var(--red)}}
.krow{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;padding:18px 28px 4px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}}
.kpi::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}}
.kpi.kw::before{{background:linear-gradient(90deg,#f0a500,#ffcc44)}}
.kpi.ka::before{{background:linear-gradient(90deg,var(--red),#e74c3c)}}
.kpi.ko::before{{background:linear-gradient(90deg,var(--green),#27ae60)}}
.kl{{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.kv{{font-size:17px;font-weight:700;letter-spacing:-.3px;line-height:1}}
.ks{{font-size:11px;color:var(--muted);margin-top:5px}}
.tsec{{padding:16px 28px 32px}}
.thead-row{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}}
.ttitle{{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.sw{{position:relative}}
.sw input{{border:2px solid var(--teal);border-radius:6px;padding:8px 12px 8px 34px;font-size:13px;width:260px;background:#fff;box-shadow:0 0 0 3px rgba(0,144,168,.08)}}
.sw input:focus{{outline:none;border-color:var(--navy)}}
.sw input::placeholder{{color:var(--teal);font-weight:500}}
.sw::before{{content:'🔍';position:absolute;left:9px;top:50%;transform:translateY(-50%);font-size:13px;pointer-events:none}}
.tw{{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}}
table{{width:100%;border-collapse:collapse;min-width:900px}}
thead th{{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:left;white-space:nowrap;cursor:pointer;user-select:none;letter-spacing:.3px;transition:background .12s}}
thead th.right{{text-align:right}}
thead th:hover{{background:var(--navy-light)}}
thead th.sorted{{color:var(--teal-light)}}
.si{{margin-left:4px;opacity:.45;font-size:9px}}
thead th.sorted .si{{opacity:1}}
tbody tr{{transition:background .1s}}
tbody tr:nth-child(even){{background:var(--row-alt)}}
tbody tr:hover{{background:var(--hover)}}
tbody tr.pendente{{background:#fff5f4!important}}
tbody tr.pendente:hover{{background:#ffe8e6!important}}
td{{padding:9px 14px;border-bottom:1px solid var(--border);white-space:nowrap;font-variant-numeric:tabular-nums}}
td.right{{text-align:right}}
td.mono{{font-family:'Consolas','Courier New',monospace;font-size:12px;color:var(--muted)}}
.vnz{{color:var(--orange);font-weight:700}}
.vz{{color:var(--muted)}}
tfoot td{{background:#e8f0f8;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;font-size:12.5px}}
.empty{{text-align:center;padding:56px;color:var(--muted)}}
.pag{{display:flex;justify-content:space-between;align-items:center;margin-top:12px;font-size:12px;color:var(--muted)}}
.pbtns{{display:flex;gap:4px}}
.pbtns button{{min-width:30px;height:30px;border:1.5px solid var(--border);border-radius:6px;background:var(--surface);cursor:pointer;font-size:12px;font-weight:600;padding:0 8px;transition:all .12s}}
.pbtns button:hover:not(:disabled):not(.active){{border-color:var(--teal);color:var(--teal)}}
.pbtns button.active{{background:var(--teal);border-color:var(--teal);color:#fff}}
.pbtns button:disabled{{opacity:.35;cursor:default}}
.badge{{display:inline-block;padding:2px 8px;border-radius:20px;font-size:10px;font-weight:700;letter-spacing:.4px;text-transform:uppercase}}
.br{{background:#fde8e6;color:var(--red)}}.bg{{background:#e6f5ec;color:var(--green)}}
.voltar{{font-size:11px;color:#7a99bb;text-decoration:none;display:flex;align-items:center;gap:4px;margin-left:20px;opacity:.8}}
.voltar:hover{{opacity:1}}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">⚖️</div>
    <h1>Contas Transitórias
      <span>SIGGO · {schema_label} · Saldos que devem zerar ao fim do exercício</span>
    </h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <span id="ts">Gerado em: {timestamp}</span>
</header>
<div class="aviso">⚠️ <strong>Atenção:</strong> As contas transitórias <strong>não podem encerrar o exercício com saldo</strong>. Use o filtro "Saldo ≠ 0" para identificar pendências a regularizar.</div>
<div class="fbar">
  <div class="fg"><label>Exercício</label><select id="fe"><option value="">Todos</option></select></div>
  <div class="fg"><label>Mês</label><select id="fm"><option value="">Todos</option></select></div>
  <div class="fg">
    <label>Unidade Gestora</label>
    <div class="ug-wrap">
      <input id="fu-input" class="ug-input" type="text" placeholder="Código ou nome…" autocomplete="off"
             oninput="onUGInput()" onfocus="onUGFocus()" onblur="onUGBlur()">
      <button class="ug-clear" id="fu-clear" onclick="limparUG()" title="Limpar">✕</button>
      <div class="ug-dd" id="fu-dd"></div>
    </div>
  </div>
  <div class="fg"><label>Tipo de Agregação</label><select id="ft"><option value="">Todos</option></select></div>
  <div class="fg"><label>Conta Contábil</label><select id="fc"><option value="">Todas</option></select></div>
  <div class="bgrp">
    <button class="btn btn-nz" id="btn-nz" onclick="toggleNZ()">⚠ Saldo ≠ 0</button>
    <button class="btn btn-g" onclick="limpar()">↺ Limpar filtros</button>
    <button class="btn btn-p" onclick="exportar()">⬇ Exportar CSV</button>
  </div>
</div>
<div class="krow" id="krow"></div>
<div class="tsec">
  <div class="thead-row">
    <span class="ttitle" id="cnt"></span>
    <div class="sw"><input id="busca" type="text" placeholder="Buscar por UG ou conta…" oninput="aplicar()"></div>
  </div>
  <div class="tw">
    <table>
      <thead>
        <tr>
          <th onclick="sort('UNIDADE_GESTORA',this)">Unidade Gestora<span class="si">↕</span></th>
          <th onclick="sort('TIPO_AGREGACAO',this)">Tipo de Agregação<span class="si">↕</span></th>
          <th onclick="sort('EXERCICIO',this)" class="right">Exercício<span class="si">↕</span></th>
          <th onclick="sort('MES',this)" class="right">Mês<span class="si">↕</span></th>
          <th onclick="sort('CONTA_CONTABIL',this)" class="right">Conta Contábil<span class="si">↕</span></th>
          <th onclick="sort('SALDO',this)" class="right">Saldo<span class="si">↕</span></th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
      <tfoot id="tfoot"></tfoot>
    </table>
  </div>
  <div class="pag" id="pag"></div>
</div>
<script>
const ALL={dados};
const MESES=['','Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'];
let fil=[],sCol='',sAsc=true,pg=1,ugSel='',filtrarNZ=false;
const PS=50;
const brl=v=>isNaN(v)?'—':Number(v).toLocaleString('pt-BR',{{style:'currency',currency:'BRL'}});
const vnz=v=>Math.abs(v)<0.005?'vz':'vnz';

const ugMap={{}};
ALL.forEach(r=>{{if(r.COUG&&!ugMap[r.COUG])ugMap[r.COUG]=r.UNIDADE_GESTORA;}});
const ugList=Object.entries(ugMap).map(([c,label])=>{{return{{c,label}}}}).sort((a,b)=>a.c.localeCompare(b.c));

function onUGInput(){{
  const v=document.getElementById('fu-input').value.toLowerCase();
  const m=v?ugList.filter(u=>u.c.includes(v)||u.label.toLowerCase().includes(v)):ugList;
  renderDD(m);if(!v){{ugSel='';document.getElementById('fu-clear').style.display='none';}}
}}
function onUGFocus(){{const v=document.getElementById('fu-input').value.toLowerCase();renderDD(v?ugList.filter(u=>u.c.includes(v)||u.label.toLowerCase().includes(v)):ugList);}}
function onUGBlur(){{setTimeout(()=>document.getElementById('fu-dd').style.display='none',200);}}
function renderDD(lista){{
  const dd=document.getElementById('fu-dd');
  if(!lista.length){{dd.innerHTML='<div class="ug-dd-empty">Nenhuma UG encontrada</div>';dd.style.display='block';return;}}
  dd.innerHTML=lista.slice(0,80).map(u=>`<div class="ug-dd-item" onmousedown="selUG('${{u.c}}','${{u.label.replace(/'/g,"\\'")}}')"><strong>${{u.c}}</strong> — ${{u.label}}</div>`).join('');
  dd.style.display='block';
}}
function selUG(c,label){{ugSel=c;document.getElementById('fu-input').value=label;document.getElementById('fu-dd').style.display='none';document.getElementById('fu-clear').style.display='block';aplicar();}}
function limparUG(){{ugSel='';document.getElementById('fu-input').value='';document.getElementById('fu-clear').style.display='none';aplicar();}}

function toggleNZ(){{
  filtrarNZ=!filtrarNZ;
  const btn=document.getElementById('btn-nz');
  btn.classList.toggle('ativo',filtrarNZ);
  btn.textContent=filtrarNZ?'⚠ Saldo ≠ 0 (ativo)':'⚠ Saldo ≠ 0';
  aplicar();
}}

function init(){{
  const uniq=(k,num)=>{{const v=[...new Set(ALL.map(r=>r[k]))];return num?v.sort((a,b)=>a-b):v.sort((a,b)=>String(a).localeCompare(String(b),'pt-BR'));}}
  fillSel('fe',uniq('EXERCICIO',true));
  fillSel('fm',uniq('MES',true).map(m=>{{return{{val:m,label:m+(MESES[m]?' — '+MESES[m]:'')}}}}));
  fillSel('ft',uniq('TIPO_AGREGACAO'));
  fillSel('fc',uniq('CONTA_CONTABIL'));
  aplicar();
}}
function fillSel(id,vals){{
  const s=document.getElementById(id),p=s.value;
  s.innerHTML='<option value="">Todos</option>';
  vals.forEach(v=>{{const o=document.createElement('option');if(typeof v==='object'&&v.val!==undefined){{o.value=v.val;o.textContent=v.label;}}else{{o.value=o.textContent=v;}}s.appendChild(o)}});
  if(p)s.value=p;
}}
function aplicar(){{
  const e=document.getElementById('fe').value,m=document.getElementById('fm').value;
  const t=document.getElementById('ft').value,c=document.getElementById('fc').value;
  const b=document.getElementById('busca').value.trim().toLowerCase();
  fil=ALL.filter(r=>{{
    if(e&&String(r.EXERCICIO)!==e)return false;
    if(m&&String(r.MES)!==m)return false;
    if(ugSel&&r.COUG!==ugSel)return false;
    if(t&&r.TIPO_AGREGACAO!==t)return false;
    if(c&&String(r.CONTA_CONTABIL)!==c)return false;
    if(filtrarNZ&&Math.abs(r.SALDO)<0.005)return false;
    if(b&&!r.UNIDADE_GESTORA.toLowerCase().includes(b)&&!String(r.CONTA_CONTABIL).toLowerCase().includes(b)&&!r.COUG.toLowerCase().includes(b))return false;
    return true;
  }});
  if(sCol)doSort();pg=1;render();kpis();
}}
function limpar(){{
  ['fe','fm','ft','fc'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('busca').value='';limparUG();
  if(filtrarNZ)toggleNZ();
}}
function sort(col,th){{
  sAsc=sCol===col?!sAsc:true;sCol=col;
  document.querySelectorAll('thead th').forEach(t=>{{t.classList.remove('sorted');const si=t.querySelector('.si');if(si)si.textContent='↕'}});
  th.classList.add('sorted');th.querySelector('.si').textContent=sAsc?'↑':'↓';doSort();pg=1;render();
}}
function doSort(){{
  fil.sort((a,b)=>{{const va=a[sCol],vb=b[sCol];if(typeof va==='number')return sAsc?va-vb:vb-va;return sAsc?String(va).localeCompare(String(vb),'pt-BR'):String(vb).localeCompare(String(va),'pt-BR')}});
}}
function render(){{
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  const nz=fil.filter(r=>Math.abs(r.SALDO)>=0.005).length;
  document.getElementById('cnt').textContent=fil.length.toLocaleString('pt-BR')+' registro'+(fil.length!==1?'s':'')+(nz>0?' · '+nz.toLocaleString('pt-BR')+' com saldo ≠ 0':'');
  if(!fil.length){{tb.innerHTML='<tr><td colspan="6" class="empty">Nenhum registro com os filtros selecionados.</td></tr>';tf.innerHTML='';document.getElementById('pag').innerHTML='';return;}}
  const rows=fil.slice((pg-1)*PS,pg*PS);
  tb.innerHTML=rows.map(r=>{{
    const p=Math.abs(r.SALDO)>=0.005;
    return`<tr class="${{p?'pendente':''}}">
      <td>${{r.UNIDADE_GESTORA}}</td>
      <td>${{r.TIPO_AGREGACAO||'—'}}</td>
      <td class="right">${{r.EXERCICIO}}</td>
      <td class="right">${{MESES[r.MES]||r.MES}}</td>
      <td class="mono right">${{r.CONTA_CONTABIL}}</td>
      <td class="right ${{vnz(r.SALDO)}}">${{brl(r.SALDO)}}</td>
    </tr>`;
  }}).join('');
  const st=fil.reduce((a,r)=>a+r.SALDO,0);
  tf.innerHTML=`<tr><td colspan="5">Totais (${{fil.length.toLocaleString('pt-BR')}} registros)</td><td class="right ${{vnz(st)}}">${{brl(st)}}</td></tr>`;
  paginar();
}}
function kpis(){{
  if(!fil.length){{document.getElementById('krow').innerHTML='';return;}}
  const st=fil.reduce((a,r)=>a+r.SALDO,0);
  const nz=fil.filter(r=>Math.abs(r.SALDO)>=0.005).length;
  const ok=fil.length-nz;
  const pct=fil.length?(nz/fil.length*100).toFixed(1):0;
  const ugs=new Set(fil.map(r=>r.COUG)).size;
  const contas=new Set(fil.map(r=>r.CONTA_CONTABIL)).size;
  const kc=nz===0?'ko':nz<fil.length*0.1?'kw':'ka';
  document.getElementById('krow').innerHTML=`
    <div class="kpi"><div class="kl">Total de Registros</div><div class="kv">${{fil.length.toLocaleString('pt-BR')}}</div><div class="ks">${{ugs}} UG${{ugs!==1?'s':''}} · ${{contas}} conta${{contas!==1?'s':''}}</div></div>
    <div class="kpi ${{kc}}"><div class="kl">Com Saldo ≠ 0 (pendências)</div><div class="kv" style="color:${{nz>0?'var(--red)':'var(--green)}}">${{nz.toLocaleString('pt-BR')}}</div><div class="ks"><span class="badge ${{nz>0?'br':'bg'}}">${{pct}}% dos registros</span></div></div>
    <div class="kpi ko"><div class="kl">Zerados (OK)</div><div class="kv" style="color:var(--green)">${{ok.toLocaleString('pt-BR')}}</div><div class="ks"><span class="badge bg">${{(100-parseFloat(pct)).toFixed(1)}}% dos registros</span></div></div>
    <div class="kpi ${{Math.abs(st)<0.01?'ko':'ka'}}"><div class="kl">Saldo Total (pendente)</div><div class="kv ${{vnz(st)}}">${{brl(st)}}</div><div class="ks">Soma dos saldos não zerados</div></div>`;
}}
function paginar(){{
  const pag=document.getElementById('pag'),pages=Math.ceil(fil.length/PS);
  if(pages<=1){{pag.innerHTML='';return;}}
  const s=(pg-1)*PS+1,e=Math.min(pg*PS,fil.length);
  let b=`<button onclick="ir(${{pg-1}})" ${{pg===1?'disabled':''}}>‹</button>`;
  for(let i=1;i<=pages;i++){{
    if(i===1||i===pages||Math.abs(i-pg)<=1)b+=`<button class="${{i===pg?'active':''}}" onclick="ir(${{i}})">${{i}}</button>`;
    else if(Math.abs(i-pg)===2)b+=`<button disabled style="border:none;background:none">…</button>`;
  }}
  b+=`<button onclick="ir(${{pg+1}})" ${{pg===pages?'disabled':''}}>›</button>`;
  pag.innerHTML=`<span>Mostrando ${{s.toLocaleString('pt-BR')}}–${{e.toLocaleString('pt-BR')}} de ${{fil.length.toLocaleString('pt-BR')}}</span><div class="pbtns">${{b}}</div>`;
}}
function ir(p){{const pages=Math.ceil(fil.length/PS);if(p<1||p>pages)return;pg=p;render();window.scrollTo({{top:0,behavior:'smooth'}})}}
function exportar(){{
  if(!fil.length)return alert('Nenhum dado para exportar.');
  const cols=['COUG','UNIDADE_GESTORA','TIPO_AGREGACAO','EXERCICIO','MES','CONTA_CONTABIL','SALDO'];
  const linhas=[cols.join(';'),...fil.map(r=>cols.map(c=>typeof r[c]==='number'?String(r[c]).replace('.',','):r[c]).join(';'))];
  const a=Object.assign(document.createElement('a'),{{href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{{type:'text/csv;charset=utf-8'}})),download:'contas_transitorias.csv'}});
  a.click();URL.revokeObjectURL(a.href);
}}
['fe','fm','ft','fc'].forEach(id=>document.getElementById(id).addEventListener('change',aplicar));
init();
</script>
</body>
</html>"""


# ── Oracle ──────────────────────────────────────────────────────────────────────
oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")


def extrair(ug: str | None, ano: str | None) -> pd.DataFrame:
    filtro_ug  = "AND sc.COUG = :ug"  if ug  else ""
    filtro_ano = "AND sc.ANO  = :ano" if ano else ""
    sql = SQL.format(filtro_ug=filtro_ug, filtro_ano=filtro_ano)
    params = {}
    if ug:  params["ug"]  = ug
    if ano: params["ano"] = ano

    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle…")
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        print(f"[{datetime.now():%H:%M:%S}] Executando consulta…")
        with conn.cursor() as cur:
            cur.execute(sql, params)
            colunas = [c[0] for c in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=colunas)

    df["SALDO"] = pd.to_numeric(df["SALDO"], errors="coerce").fillna(0)
    df["EXERCICIO"] = df["EXERCICIO"].astype(int)
    df["MES"]       = df["MES"].astype(int)
    df["CONTA_CONTABIL"] = df["CONTA_CONTABIL"].astype(str)
    df["COUG"] = df["COUG"].astype(str)

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

    return HTML_TEMPLATE.format(
        dados=json.dumps(registros, ensure_ascii=False),
        timestamp=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        schema_label=SCHEMA.rstrip("."),
    )


def publicar_github(caminho: str, mensagem_commit: str) -> None:
    pasta = Path(__file__).parent
    url_remote = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"

    def git(*args):
        r = subprocess.run(["git", "-C", str(pasta)] + list(args), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        return r.stdout.strip()

    print(f"[{datetime.now():%H:%M:%S}] Publicando no GitHub…")
    try:
        git("remote", "set-url", "origin", url_remote)
    except RuntimeError:
        git("remote", "add", "origin", url_remote)

    git("add", caminho)
    git("commit", "-m", mensagem_commit)
    git("push", "origin", GITHUB_BRANCH)

    print(f"[{datetime.now():%H:%M:%S}] Publicado com sucesso.")
    print(f"  → https://{GITHUB_USER}.github.io/{GITHUB_REPO}/{caminho}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Contas Transitórias — extrai do Oracle e publica no GitHub Pages.")
    parser.add_argument("--ug",      type=str, default=None,       help="Filtrar por Unidade Gestora")
    parser.add_argument("--ano",     type=str, default=None,       help="Filtrar por exercício (ex: 2026)")
    parser.add_argument("--out",     type=str, default=ARQUIVO_HTML, help="Arquivo HTML de saída")
    parser.add_argument("--no-push", action="store_true", default=False, help="Gera HTML sem publicar no GitHub")
    args = parser.parse_args()

    try:
        df = extrair(args.ug, args.ano)
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
            mensagem_commit=f"chore: atualiza contas transitórias — {ts}",
        )

    def brl(v: float) -> str:
        return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

    com_saldo = int((df["SALDO"].abs() > 0.005).sum())
    print("\n── Resumo ─────────────────────────────────────────────────────────")
    print(f"  Total de registros    : {len(df):,}")
    print(f"  Com saldo ≠ 0         : {com_saldo:,} de {len(df):,}")
    print(f"  Saldo total           : {brl(float(df['SALDO'].sum()))}")
    print("───────────────────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
