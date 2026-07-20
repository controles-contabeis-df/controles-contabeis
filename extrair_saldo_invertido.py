"""
Saldo Invertido — SIGGO/Oracle
Extrai contas de classes 1 a 8 com saldo no sentido contrário ao esperado,
gera HTML autocontido e publica no GitHub Pages.

Dependências:
    pip install oracledb pandas

Uso:
    python extrair_saldo_invertido.py
    python extrair_saldo_invertido.py --no-push   (gera HTML sem publicar)
    python extrair_saldo_invertido.py --ano 2026
"""

import argparse
import gzip
import base64
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import oracledb
import pandas as pd

import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ORACLE_USER   = os.environ["ORACLE_USER"]
ORACLE_PASS   = os.environ["ORACLE_PASS"]
ORACLE_DSN    = os.environ["ORACLE_DSN"]
SCHEMA        = "MIL2026."

GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
GITHUB_USER   = os.environ["GITHUB_USER"]
GITHUB_REPO   = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = os.environ["GITHUB_BRANCH"]

ARQUIVO_HTML  = "saldo_invertido.html"

SQL = f"""
SELECT
    SALDO.COUG,
    SALDO.COGESTAO,
    NVL(g.NOGESTAO, 'Sem nome')                                            AS NOGESTAO,
    NVL(UG.NOUG, 'Sem nome')                                               AS NOUG,
    CONTACONTABIL.NOCONTACONTABIL,
    CONTACONTABIL.INSISCONTABIL,
    SALDO.COCONTACONTABIL,
    SALDO.COCONTACORRENTE,
    CASE
        WHEN CONTACONTABIL.INSALDOCONTABIL = 'D' THEN
            SUM(SALDO.VADEBITO) - SUM(SALDO.VACREDITO)
        WHEN CONTACONTABIL.INSALDOCONTABIL = 'C' THEN
            SUM(SALDO.VACREDITO) - SUM(SALDO.VADEBITO)
        ELSE
            SUM(SALDO.VACREDITO) - SUM(SALDO.VADEBITO)
    END AS SALDO_CALCULADO
FROM
    {SCHEMA}VCONTACONTABIL CONTACONTABIL
    JOIN {SCHEMA}VSALDOCONTABIL SALDO
        ON CONTACONTABIL.COCONTACONTABIL = SALDO.COCONTACONTABIL
    LEFT JOIN {SCHEMA}UNIDADEGESTORA UG
        ON SALDO.COUG = UG.COUG
    LEFT JOIN {SCHEMA}GESTAO g
        ON g.COGESTAO = SALDO.COGESTAO
WHERE
    CONTACONTABIL.ININVERSAOSALDO = 'N'
    AND CONTACONTABIL.COCONTACONTABIL BETWEEN 100000000 AND 899999999
    AND CONTACONTABIL.INESCRITURACAO = 'S'
    AND CONTACONTABIL.INSALDOCONTABIL IN ('D', 'C')
    {{filtro_ug}}
GROUP BY
    CONTACONTABIL.ININVERSAOSALDO,
    SALDO.COCONTACONTABIL,
    CONTACONTABIL.NOCONTACONTABIL,
    CONTACONTABIL.INSISCONTABIL,
    CONTACONTABIL.INSALDOCONTABIL,
    CONTACONTABIL.INESCRITURACAO,
    SALDO.COCONTACORRENTE,
    SALDO.COUG,
    SALDO.COGESTAO,
    UG.NOUG,
    g.NOGESTAO
HAVING
    CASE
        WHEN CONTACONTABIL.INSALDOCONTABIL = 'D' THEN
            SUM(SALDO.VADEBITO) - SUM(SALDO.VACREDITO)
        WHEN CONTACONTABIL.INSALDOCONTABIL = 'C' THEN
            SUM(SALDO.VACREDITO) - SUM(SALDO.VADEBITO)
        ELSE
            SUM(SALDO.VACREDITO) - SUM(SALDO.VADEBITO)
    END < 0
ORDER BY
    SALDO.COUG,
    SALDO.COGESTAO,
    SALDO.COCONTACONTABIL,
    SALDO.COCONTACORRENTE
"""

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Saldo Invertido — Controle de Contas</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --navy:#0d1b3e;--navy-mid:#162550;--navy-light:#1e3267;
  --teal:#0090a8;--teal-light:#00b8d4;
  --surface:#fff;--bg:#f2f5f9;--border:#dce3ed;
  --row-alt:#f7f9fc;--hover:#eaf4f7;
  --text:#1a2033;--muted:#6b7a99;
  --red:#c0392b;--green:#1a7a44;--radius:10px;
  --shadow:0 2px 12px rgba(13,27,62,.10);
}}
body{{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}}
header{{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}}
.hlogo{{width:32px;height:32px;background:var(--teal);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}}
header h1{{font-size:14px;font-weight:700;letter-spacing:.6px;text-transform:uppercase}}
header h1 span{{font-weight:400;color:#9ab0cc;font-size:12px;display:block;text-transform:none;letter-spacing:0;margin-top:1px}}
#ts{{font-size:11px;color:#7a99bb;white-space:nowrap}}
.aviso{{background:#fff8e6;border-bottom:1px solid #f0c060;padding:8px 28px;font-size:11.5px;color:#7a5c00;display:flex;align-items:center;gap:8px}}
.fbar{{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}}
.fg{{display:flex;flex-direction:column;gap:4px}}
.fg label{{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}}
.fg select{{border:1.5px solid var(--border);border-radius:6px;padding:7px 28px 7px 10px;font-size:12.5px;min-width:160px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E") no-repeat right 9px center;color:var(--text);cursor:pointer;appearance:none}}
.fg select:focus{{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}}
.ac-wrap{{position:relative;min-width:240px}}
.ac-input{{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text);transition:border-color .15s}}
.ac-input:focus{{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}}
.ac-clear{{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none}}
.ac-dd{{position:absolute;top:calc(100% + 4px);left:0;right:0;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:240px;overflow-y:auto;display:none}}
.ac-dd-item{{padding:8px 12px;cursor:pointer;font-size:12px;border-bottom:1px solid var(--border);transition:background .1s}}
.ac-dd-item:last-child{{border-bottom:none}}
.ac-dd-item:hover{{background:var(--hover)}}
.ac-dd-item strong{{color:var(--navy);font-weight:700}}
.ac-dd-empty{{padding:12px;color:var(--muted);font-size:12px;text-align:center}}
.bgrp{{display:flex;gap:8px;margin-left:auto;align-items:flex-end;flex-wrap:wrap}}
.btn{{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;transition:filter .15s,transform .1s;white-space:nowrap}}
.btn:hover{{filter:brightness(1.08);transform:translateY(-1px)}}
.btn-p{{background:var(--teal);color:#fff}}
.btn-g{{background:var(--border);color:var(--text)}}
.tsec{{padding:16px 28px 32px}}
.thead-row{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}}
.ttitle{{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.sw{{position:relative}}
.sw input{{border:2px solid var(--teal);border-radius:6px;padding:8px 12px 8px 34px;font-size:13px;width:280px;background:#fff;box-shadow:0 0 0 3px rgba(0,144,168,.08)}}
.sw input:focus{{outline:none;border-color:var(--navy)}}
.sw input::placeholder{{color:var(--teal);font-weight:500}}
.sw::before{{content:'🔍';position:absolute;left:9px;top:50%;transform:translateY(-50%);font-size:13px;pointer-events:none}}
.tw{{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}}
table{{width:100%;border-collapse:collapse;min-width:900px}}
thead th{{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:left;white-space:nowrap;letter-spacing:.3px}}
thead th.right{{text-align:right}}
tbody tr{{transition:background .1s}}
tbody tr:hover td{{background:var(--hover)!important}}
td{{padding:9px 14px;border-bottom:1px solid var(--border);white-space:nowrap;font-variant-numeric:tabular-nums}}
td.right{{text-align:right}}
td.mono{{font-family:'Consolas','Courier New',monospace;font-size:12px}}
.vneg{{color:var(--red);font-weight:700}}
tfoot td{{background:#e8f0f8;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;font-size:12.5px}}
.empty{{text-align:center;padding:56px;color:var(--muted)}}
/* Grupos */
tr.grp-l1{{background:var(--navy);cursor:pointer}}
tr.grp-l1 td{{color:#e8f0fc;font-weight:700;padding:10px 14px}}
tr.grp-l1:hover td{{background:var(--navy-mid)!important}}
tr.grp-l2{{background:#1e3267;cursor:pointer}}
tr.grp-l2 td{{color:#c8d8ec;font-weight:700;padding:9px 14px 9px 28px}}
tr.grp-l2:hover td{{background:#162550!important}}
tr.grp-l3{{background:#2a4580;cursor:pointer}}
tr.grp-l3 td{{color:#d4e4f8;font-weight:600;padding:8px 14px 8px 42px}}
tr.grp-l3:hover td{{background:#1e3267!important}}
tr.grp-l4{{background:#e8f0fb;cursor:pointer}}
tr.grp-l4 td{{color:var(--navy);font-weight:600;padding:8px 14px 8px 56px}}
tr.grp-l4:hover td{{background:#d8e6f8!important}}
tr.grp-l5 td{{padding:7px 14px 7px 70px;background:var(--surface)}}
tr.grp-l5:nth-child(even) td{{background:var(--row-alt)}}
.tog{{margin-right:6px;font-style:normal;display:inline-block;width:12px}}
.voltar{{font-size:11px;color:#7a99bb;text-decoration:none;display:flex;align-items:center;gap:4px;margin-left:20px;opacity:.8}}
.voltar:hover{{opacity:1}}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">🔄</div>
    <h1>Saldo Invertido
      <span>SIGGO · Ano Exercício 2026 · Contas com saldo invertido</span>
    </h1>
    <a class="voltar" href="index.html">← Painel inicial</a>
  </div>
  <span id="ts">Gerado em: {timestamp}</span>
</header>
<div class="aviso">⚠️ <strong>Atenção:</strong> Registros indicam saldos invertidos, passíveis de conferência com vistas a sua regularização.</div>
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
    <div class="ac-wrap">
      <input id="fu-input" class="ac-input" type="text" placeholder="Código ou nome…" autocomplete="off"
             oninput="onACInput('u')" onfocus="onACFocus('u')" onblur="onACBlur('u')">
      <button class="ac-clear" id="fu-clear" onclick="limparAC('u')" title="Limpar">✕</button>
      <div class="ac-dd" id="fu-dd"></div>
    </div>
  </div>
  <div class="fg">
    <label>Conta Contábil</label>
    <div class="ac-wrap" style="min-width:300px">
      <input id="fc-input" class="ac-input" type="text" placeholder="Código ou nome…" autocomplete="off"
             oninput="onACInput('c')" onfocus="onACFocus('c')" onblur="onACBlur('c')">
      <button class="ac-clear" id="fc-clear" onclick="limparAC('c')" title="Limpar">✕</button>
      <div class="ac-dd" id="fc-dd"></div>
    </div>
  </div>
  <div class="fg"><label>Grupo de Contas</label>
    <select id="fg-grupo">
      <option value="">Todos</option>
      <option value="Ativo Financeiro">Ativo Financeiro</option>
      <option value="Passivo Financeiro">Passivo Financeiro</option>
      <option value="Contas Orçamentárias">Contas Orçamentárias</option>
      <option value="Contas de Controle">Contas de Controle</option>
      <option value="Contas Patrimoniais">Contas Patrimoniais</option>
    </select>
  </div>
  <div class="bgrp">
    <button class="btn btn-g" onclick="limpar()">↺ Limpar filtros</button>
    <button class="btn btn-p" onclick="exportar()">⬇ Exportar CSV</button>
  </div>
</div>
<div class="tsec">
  <div class="thead-row">
    <span class="ttitle" id="cnt"></span>
    <div class="sw"><input id="busca" type="text" placeholder="Buscar…" oninput="aplicar()"></div>
  </div>
  <div class="tw">
    <table>
      <thead>
        <tr>
          <th>Gestão / UG / Conta / Conta Corrente</th>
          <th class="right">Saldo Calculado</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
      <tfoot id="tfoot"></tfoot>
    </table>
  </div>
</div>
<script>
(async()=>{{
const gz='{dados_gz}';
const bin=atob(gz);
const bytes=new Uint8Array(bin.length);
for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);
const ds=new DecompressionStream('gzip');
const writer=ds.writable.getWriter();writer.write(bytes);writer.close();
const out=await new Response(ds.readable).arrayBuffer();
const ALL=JSON.parse(new TextDecoder().decode(out));

// Campos derivados
ALL.forEach(r=>{{
  r.CONTA_LABEL=r.COCONTACONTABIL+(r.NOCONTACONTABIL?' - '+r.NOCONTACONTABIL:'');
  r.GESTAO_LABEL=r.COGESTAO+(r.NOGESTAO?' - '+r.NOGESTAO:'');
  const co=String(r.COCONTACONTABIL),sys=r.INSISCONTABIL||'';
  if(sys==='F'&&co.startsWith('1'))r.GRUPO='Ativo Financeiro';
  else if(sys==='F'&&co.startsWith('22'))r.GRUPO='Passivo Financeiro';
  else if(sys==='O')r.GRUPO='Contas Orçamentárias';
  else if(sys==='C')r.GRUPO='Contas de Controle';
  else r.GRUPO='Contas Patrimoniais';
}});

const brl=v=>isNaN(v)?'—':Number(v).toLocaleString('pt-BR',{{style:'currency',currency:'BRL'}});

// Listas para autocomplete
function buildList(keyFn,labelFn){{
  const m={{}};
  ALL.forEach(r=>{{const k=keyFn(r);if(k&&!m[k])m[k]=labelFn(r);}});
  return Object.entries(m).map(([c,label])=>{{return{{c,label}}}}).sort((a,b)=>a.c.localeCompare(b.c,'pt-BR'));
}}
const gestaoList=buildList(r=>r.COGESTAO,r=>r.GESTAO_LABEL);
const ugList    =buildList(r=>r.COUG,    r=>r.UG);
const contaList =buildList(r=>r.COCONTACONTABIL,r=>r.CONTA_LABEL);

// Estado dos autocompletes
const acState={{'g':{{sel:'',list:gestaoList,inp:'fg-input',clr:'fg-clear',dd:'fg-dd'}},
                'u':{{sel:'',list:ugList,    inp:'fu-input',clr:'fu-clear',dd:'fu-dd'}},
                'c':{{sel:'',list:contaList, inp:'fc-input',clr:'fc-clear',dd:'fc-dd'}}}};

function onACInput(k){{
  const st=acState[k],v=document.getElementById(st.inp).value.toLowerCase();
  const m=v?st.list.filter(x=>x.c.includes(v)||x.label.toLowerCase().includes(v)):st.list;
  renderACDD(k,m);if(!v){{st.sel='';document.getElementById(st.clr).style.display='none';}}
}}
function onACFocus(k){{
  const st=acState[k],v=document.getElementById(st.inp).value.toLowerCase();
  renderACDD(k,v?st.list.filter(x=>x.c.includes(v)||x.label.toLowerCase().includes(v)):st.list);
}}
function onACBlur(k){{setTimeout(()=>document.getElementById(acState[k].dd).style.display='none',200);}}
function renderACDD(k,lista){{
  const dd=document.getElementById(acState[k].dd);
  if(!lista.length){{dd.innerHTML='<div class="ac-dd-empty">Nenhum resultado</div>';dd.style.display='block';return;}}
  dd.innerHTML=lista.slice(0,100).map(x=>{{
    const sep=x.label.indexOf(' - ');
    const dispHtml=sep>=0?`<strong>${{x.label.slice(0,sep)}}</strong> —${{x.label.slice(sep+2)}}`:x.label;
    return `<div class="ac-dd-item" onmousedown="selAC('${{k}}','${{x.c}}','${{x.label.replace(/\\/g,'\\\\').replace(/'/g,"\\'")}}')">${{dispHtml}}</div>`;
  }}).join('');
  dd.style.display='block';
}}
function selAC(k,c,label){{
  const st=acState[k];st.sel=c;
  document.getElementById(st.inp).value=label;
  document.getElementById(st.dd).style.display='none';
  document.getElementById(st.clr).style.display='block';
  aplicar();
}}
function limparAC(k){{
  const st=acState[k];st.sel='';
  document.getElementById(st.inp).value='';
  document.getElementById(st.clr).style.display='none';
  aplicar();
}}

let fil=[];

function aplicar(){{
  const grupo=document.getElementById('fg-grupo').value;
  const b    =document.getElementById('busca').value.trim().toLowerCase();
  fil=ALL.filter(r=>{{
    if(grupo&&r.GRUPO!==grupo)return false;
    if(acState['g'].sel&&r.COGESTAO!==acState['g'].sel)return false;
    if(acState['u'].sel&&r.COUG!==acState['u'].sel)return false;
    if(acState['c'].sel&&r.COCONTACONTABIL!==acState['c'].sel)return false;
    if(b&&!r.GESTAO_LABEL.toLowerCase().includes(b)&&!r.UG.toLowerCase().includes(b)&&!r.CONTA_LABEL.toLowerCase().includes(b))return false;
    return true;
  }});
  render();
}}

function limpar(){{
  document.getElementById('fg-grupo').value='';
  document.getElementById('busca').value='';
  ['g','u','c'].forEach(k=>limparAC(k));
  aplicar();
}}

function fillSel(id,vals){{
  const s=document.getElementById(id);
  s.innerHTML='<option value="">Todos</option>';
  vals.forEach(v=>{{const o=document.createElement('option');o.value=o.textContent=v;s.appendChild(o);}});
}}

function init(){{
  document.getElementById('fg-grupo').addEventListener('change',aplicar);
  aplicar();
}}

function render(){{
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  const total=fil.reduce((a,r)=>a+r.SALDO_CALCULADO,0);
  document.getElementById('cnt').textContent=fil.length.toLocaleString('pt-BR')+' linha'+(fil.length!==1?'s':'');
  if(!fil.length){{tb.innerHTML='<tr><td colspan="2" class="empty">Nenhum registro com os filtros selecionados.</td></tr>';tf.innerHTML='';return;}}

  // Construir árvore: Gestão → UG → Conta → linhas CC
  const tree={{}};
  fil.forEach(r=>{{
    const g=r.GESTAO_LABEL,u=r.UG,co=r.CONTA_LABEL;
    if(!tree[g])tree[g]={{s:0,ch:{{}}}};
    if(!tree[g].ch[u])tree[g].ch[u]={{s:0,ch:{{}}}};
    if(!tree[g].ch[u].ch[co])tree[g].ch[u].ch[co]={{s:0,rows:[]}};
    tree[g].ch[u].ch[co].rows.push(r);
    tree[g].ch[u].ch[co].s+=r.SALDO_CALCULADO;
    tree[g].ch[u].s+=r.SALDO_CALCULADO;
    tree[g].s+=r.SALDO_CALCULADO;
  }});

  let html='',idx=0;
  const srt=o=>Object.keys(o).sort((a,b)=>a.localeCompare(b,'pt-BR'));

  srt(tree).forEach(g=>{{
    const gId='r'+(idx++);const gNode=tree[g];
    html+=`<tr class="grp-l1" data-id="${{gId}}" onclick="tog('${{gId}}')"><td><span class="tog">▶</span>${{g}}</td><td class="right vneg">${{brl(gNode.s)}}</td></tr>`;
    srt(gNode.ch).forEach(u=>{{
      const uId='r'+(idx++);const uNode=gNode.ch[u];
      html+=`<tr class="grp-l2" data-id="${{uId}}" data-p="${{gId}}" style="display:none" onclick="tog('${{uId}}')"><td><span class="tog">▶</span>${{u}}</td><td class="right vneg">${{brl(uNode.s)}}</td></tr>`;
      srt(uNode.ch).forEach(co=>{{
        const coId='r'+(idx++);const coNode=uNode.ch[co];
        html+=`<tr class="grp-l3" data-id="${{coId}}" data-p="${{uId}}" style="display:none" onclick="tog('${{coId}}')"><td><span class="tog">▶</span>${{co}}</td><td class="right vneg">${{brl(coNode.s)}}</td></tr>`;
        coNode.rows.sort((a,b)=>String(a.COCONTACORRENTE||'').localeCompare(String(b.COCONTACORRENTE||''),'pt-BR'));
        coNode.rows.forEach(r=>{{
          html+=`<tr class="grp-l4" data-p="${{coId}}" style="display:none"><td class="mono" style="padding-left:70px"><span style="color:var(--muted);font-weight:400;font-family:inherit">Conta corrente&nbsp;&nbsp;</span>${{r.COCONTACORRENTE||'—'}}</td><td class="right vneg">${{brl(r.SALDO_CALCULADO)}}</td></tr>`;
        }});
      }});
    }});
  }});

  tb.innerHTML=html;
  tf.innerHTML=`<tr><td>Total geral (${{fil.length.toLocaleString('pt-BR')}} linhas)</td><td class="right vneg">${{brl(total)}}</td></tr>`;
}}

function tog(id){{
  const row=document.querySelector(`tr[data-id="${{id}}"]`);
  const togEl=row.querySelector('.tog');
  const expanded=togEl.textContent==='▼';
  if(expanded){{
    collapseDesc(id);togEl.textContent='▶';
  }}else{{
    document.querySelectorAll(`tr[data-p="${{id}}"]`).forEach(r=>{{r.style.display='';const t=r.querySelector('.tog');if(t)t.textContent='▶';}});
    togEl.textContent='▼';
  }}
}}
function collapseDesc(id){{
  document.querySelectorAll(`tr[data-p="${{id}}"]`).forEach(r=>{{
    const cid=r.getAttribute('data-id');if(cid)collapseDesc(cid);
    r.style.display='none';const t=r.querySelector('.tog');if(t)t.textContent='▶';
  }});
}}

function exportar(){{
  if(!fil.length)return alert('Nenhum dado para exportar.');
  const cols=['TIPO_AGREGACAO','GESTAO_LABEL','COUG','UG','CONTA_LABEL','COCONTACORRENTE','SALDO_CALCULADO'];
  const hdr=['Tipo Agregacao','Gestao','Cod UG','Unidade Gestora','Conta Contabil','Conta Corrente','Saldo Calculado'];
  const cel=v=>{{if(typeof v==='number')return String(v).replace('.',',');const s=v??'';return /^\d+$/.test(s)?`="${{s}}"`:s;}};
  const linhas=[hdr.join(';'),...fil.map(r=>cols.map(c=>cel(r[c])).join(';'))];
  const a=Object.assign(document.createElement('a'),{{href:URL.createObjectURL(new Blob(['﻿'+linhas.join('\n')],{{type:'text/csv;charset=utf-8'}})),download:'saldo_invertido.csv'}});
  a.click();URL.revokeObjectURL(a.href);
}}

// Expõe funções ao escopo global para os handlers onclick/oninput do HTML
window.tog=tog;window.collapseDesc=collapseDesc;
window.onACInput=onACInput;window.onACFocus=onACFocus;window.onACBlur=onACBlur;
window.selAC=selAC;window.limparAC=limparAC;
window.aplicar=aplicar;window.limpar=limpar;window.exportar=exportar;
init();
}})();
</script>
</body>
</html>"""


oracledb.init_oracle_client(lib_dir=r"C:\oracle\instantclient_23_0")


def extrair(ug: str | None) -> pd.DataFrame:
    filtro_ug = "AND SALDO.COUG = :ug" if ug else ""
    sql = SQL.format(filtro_ug=filtro_ug)
    params = {}
    if ug: params["ug"] = ug

    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle...")
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        print(f"[{datetime.now():%H:%M:%S}] Executando consulta...")
        with conn.cursor() as cur:
            cur.execute(sql, params)
            colunas = [c[0] for c in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=colunas)

    df["SALDO_CALCULADO"]  = pd.to_numeric(df["SALDO_CALCULADO"], errors="coerce").fillna(0)
    df["COCONTACONTABIL"]  = df["COCONTACONTABIL"].astype(str)
    df["COUG"]             = df["COUG"].astype(str)
    df["COGESTAO"]         = df["COGESTAO"].astype(str).str.zfill(6)

    df["UG"] = df["COUG"] + " - " + df["NOUG"].fillna("Sem nome")
    df = df.drop(columns=["NOUG"])

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

    json_bytes = json.dumps(registros, ensure_ascii=False).encode("utf-8")
    gz_b64 = base64.b64encode(gzip.compress(json_bytes)).decode("ascii")

    return HTML_TEMPLATE.format(
        dados_gz=gz_b64,
        timestamp=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    )


def publicar_github(caminho: str, mensagem_commit: str) -> None:
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
    parser = argparse.ArgumentParser(description="Saldo Invertido — extrai do Oracle e publica no GitHub Pages.")
    parser.add_argument("--ug",      type=str, default=None,         help="Filtrar por Unidade Gestora")
    parser.add_argument("--out",     type=str, default=ARQUIVO_HTML, help="Arquivo HTML de saída")
    parser.add_argument("--no-push", action="store_true", default=False, help="Gera HTML sem publicar no GitHub")
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
            mensagem_commit=f"chore: atualiza saldo invertido -- {ts}",
        )

    com_saldo = int((df["SALDO_CALCULADO"] < 0).sum())
    brl = lambda v: f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    print("\n== Resumo ==")
    print(f"  Total de registros    : {len(df):,}")
    print(f"  UGs afetadas          : {df['COUG'].nunique():,}")
    print(f"  Contas unicas         : {df['COCONTACONTABIL'].nunique():,}")
    print(f"  Saldo total invertido : {brl(float(df['SALDO_CALCULADO'].sum()))}")
    print("=" * 50)


if __name__ == "__main__":
    main()
