"""
Conciliação SIGGO × SISGEPAT — painel HTML
Gera painel web autocontido com os resultados da conciliação patrimonial.

Uso:
    python extrair_conciliacao_bens_imoveis_sisgepat.py
    python extrair_conciliacao_bens_imoveis_sisgepat.py --mes 7 --ano 2026
    python extrair_conciliacao_bens_imoveis_sisgepat.py --no-push
"""

import argparse, base64, gzip, importlib.util, json, subprocess, sys
from datetime import datetime, date
from pathlib import Path

import oracledb
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ORACLE_USER   = os.environ["ORACLE_USER"]
ORACLE_PASS   = os.environ["ORACLE_PASS"]
ORACLE_DSN    = os.environ["ORACLE_DSN"]
GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
GITHUB_USER   = os.environ["GITHUB_USER"]
GITHUB_REPO   = os.environ["GITHUB_REPO"]
GITHUB_BRANCH = os.environ["GITHUB_BRANCH"]
ARQUIVO_HTML  = "conciliacao_bens_imoveis_sisgepat.html"

INSTANT_CLIENT_DIR = r"C:\oracle\instantclient_23_0"

# ── Carrega módulo de conciliação (lógica de negócio) ─────────────────────────
_CORE_PATH = Path(__file__).parent.parent / "Concilicacao_SISGEPAT" / "conciliacao_siggo_sisgepat.py"
_spec = importlib.util.spec_from_file_location("conciliacao_core", str(_CORE_PATH))
_core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_core)

# Override: usa credenciais do .env (não do config_local do colega)
_h, _, _rest = ORACLE_DSN.rpartition(":")
_port_svc    = _rest.split("/", 1)
_core.DB_USER             = ORACLE_USER
_core.DB_PASSWORD         = ORACLE_PASS
_core.DB_HOST             = _h
_core.DB_PORT             = int(_port_svc[0]) if _port_svc[0].isdigit() else 1521
_core.DB_SERVICE          = _port_svc[1] if len(_port_svc) > 1 else _rest
_core.INSTANT_CLIENT_DIR  = INSTANT_CLIENT_DIR

oracledb.init_oracle_client(lib_dir=INSTANT_CLIENT_DIR)

MESES = {1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
         7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}


# ── Extração e montagem ────────────────────────────────────────────────────────
def extrair(mes, ano):
    data_corte = f"{ano+1:04d}-01-01" if mes == 12 else f"{ano:04d}-{mes+1:02d}-01"
    print(f"[{datetime.now():%H:%M:%S}] Extraindo dados ({MESES[mes]}/{ano})…")
    df_t, df_e, df_o, df_s1, df_si = _core.extrair_via_oracle(data_corte, mes, ano)
    print(f"[{datetime.now():%H:%M:%S}] Montando base…")
    base      = _core.montar_base_sisgepat(df_t, df_e, df_o)
    # Regra 7 corrigida: obras sem terreno → 123211000 MOBILIÁRIO URBANO (não PRÉDIOS)
    mask_r7 = (base["TIPO"] == "O") & (base["COD"] == "00") & (base["_T"] == "O")
    base.loc[mask_r7, "CONTA_SISGEPAT"] = "123211000"
    siggo_ug, siggo_total, siggo_tomb = _core.montar_base_siggo(df_s1, df_si)
    quadro    = _core.montar_quadro_conciliacao(base, siggo_ug)

    # Enriquecer quadro com GESTAO (vem de VSALDOCONTABIL via df_s1)
    def _pad_gestao(v):
        try:
            return str(int(float(str(v)))).zfill(6)
        except Exception:
            return str(v).zfill(6) if str(v).isdigit() else str(v)
    ug_gestao = (
        df_s1[["COD_UG", "GESTAO"]].dropna()
        .assign(COD_UG=lambda d: d["COD_UG"].astype(str).str.zfill(6),
                GESTAO=lambda d: d["GESTAO"].astype(str).apply(_pad_gestao))
        .drop_duplicates("COD_UG")
        .set_index("COD_UG")["GESTAO"].to_dict()
    )
    quadro["GESTAO"] = quadro["UG"].map(ug_gestao).fillna("000000")

    # Buscar nome e INTIPOADM das gestões; filtrar quadro
    try:
        with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as _gc:
            _cursor = _gc.cursor()
            _cursor.execute("SELECT COGESTAO, NOGESTAO, INTIPOADM FROM MIL2026.GESTAO")
            _gest_rows = [r for r in _cursor if r[0] is not None]
        _gest_map  = {str(int(float(str(r[0])))).zfill(6): str(r[1]).strip() if r[1] else "" for r in _gest_rows}
        _gest_tipo = {str(int(float(str(r[0])))).zfill(6): str(r[2]).strip() if r[2] else "" for r in _gest_rows}
    except Exception:
        _gest_map  = {}
        _gest_tipo = {}
    quadro["NOGESTAO"] = quadro["GESTAO"].map(_gest_map).fillna("")
    # Manter apenas gestões com INTIPOADM = '1' (Direta/Tesouro) ou '7' (Fundo)
    # e excluir explicitamente a UG 010101 (Câmara Legislativa)
    quadro["_TIPO"] = quadro["GESTAO"].map(_gest_tipo).fillna("")
    quadro = quadro[quadro["_TIPO"].isin(["1", "7"])].drop(columns=["_TIPO"]).copy()
    quadro = quadro[quadro["UG"] != "010101"].copy()

    eventos   = _core.montar_eventos_pendentes(base, siggo_tomb)
    transf    = _core.montar_transferencias_pendentes(base, siggo_tomb)
    achados   = _core.auditoria_integridade(base, siggo_ug, siggo_total, quadro,
                                             siggo_tomb=siggo_tomb, eventos=eventos,
                                             transferencias=transf)
    return quadro, eventos, transf, achados, siggo_tomb


def _resumo_ug(quadro, eventos, siggo_tomb):
    """Tabela Adm. Direta × contas conciliáveis, agregada por UG."""
    q = quadro[(quadro["TIPO_UG"] == "Direta") & (quadro["CATEGORIA"] == "SISGEPAT")]
    res = (q.groupby("UG", as_index=False)
             .agg(NOME_UG=("NOME_UG","first"),
                  SISGEPAT=("SISGEPAT_VALOR","sum"),
                  SIGGO=("SIGGO_VALOR","sum"),
                  DIF=("DIFERENCA","sum")))
    res["OK"] = res["DIF"].abs() < 0.01

    # inscrição genérica por UG (contas conciliáveis)
    ger = siggo_tomb.attrs.get("generico")
    if ger is not None and len(ger):
        gc = ger[ger.get("CONCILIAVEL", ger["CONTA_CONTABIL"].isin(_core.CONTAS_SISGEPAT).map({True:"SIM",False:"NAO"})) == "SIM"] \
             if "CONCILIAVEL" in ger.columns else ger[ger["CONTA_CONTABIL"].isin(_core.CONTAS_SISGEPAT)]
        gsum = gc.groupby("COD_UG", as_index=False)["VALOR"].sum().rename(columns={"COD_UG":"UG","VALOR":"GENERICO"})
        res = res.merge(gsum, on="UG", how="left")
    if "GENERICO" not in res.columns:
        res["GENERICO"] = 0.0
    res["GENERICO"] = res["GENERICO"].fillna(0.0)

    if eventos is not None and len(eventos):
        ev_n = eventos.groupby("UG", as_index=False).size().rename(columns={"size":"N_EV"})
        res = res.merge(ev_n, on="UG", how="left")
    if "N_EV" not in res.columns:
        res["N_EV"] = 0
    res["N_EV"] = res["N_EV"].fillna(0).astype(int)
    return res.sort_values("DIF", key=lambda s: s.abs(), ascending=False)


def _embed(data):
    js = json.dumps(data, ensure_ascii=False, default=str)
    gz = gzip.compress(js.encode("utf-8"), compresslevel=9)
    return base64.b64encode(gz).decode("ascii")


# ── Geração do HTML ───────────────────────────────────────────────────────────
def gerar_html(quadro, eventos, transf, achados, siggo_tomb, mes, ano):  # noqa: C901
    tot_sis   = float(quadro["SISGEPAT_VALOR"].sum())
    tot_sig   = float(quadro["SIGGO_VALOR"].sum())
    div_reais = float(quadro["DIFERENCA"].sum())

    mes_label = MESES[mes]
    meta = dict(ano=ano, mes=mes, mes_label=mes_label, tot_sis=tot_sis, tot_sig=tot_sig,
                div_reais=div_reais, emitido=datetime.now().strftime("%d/%m/%Y %H:%M:%S"))

    def _q(df, cols):
        return df[cols].where(pd.notnull(df[cols]), None).to_dict(orient="records")

    quadro_rows = _q(quadro.rename(columns={
        "NOME_UG": "NOME", "DESCRICAO_CONTA": "DESC",
        "SISGEPAT_VALOR": "SIS", "SIGGO_VALOR": "SIG", "DIFERENCA": "DIF"}),
        ["UG", "NOME", "CONTA", "DESC", "SIS", "SIG", "DIF"])

    b64_meta   = _embed(meta)
    b64_quadro = _embed(quadro_rows)


    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Conciliacao SIGGO e SISGEPAT - Bens Imoveis</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--navy:#0d1b3e;--navy-mid:#162550;--navy-light:#1e3267;--teal:#0090a8;--teal-light:#00b8d4;--surface:#fff;--bg:#f2f5f9;--border:#dce3ed;--row-alt:#f7f9fc;--hover:#eaf4f7;--text:#1a2033;--muted:#6b7a99;--red:#c0392b;--green:#1a7a44;--radius:10px;--shadow:0 2px 12px rgba(13,27,62,.10)}}
body{{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px;min-height:100vh}}
header{{background:linear-gradient(135deg,var(--navy) 0%,var(--navy-light) 100%);color:#fff;padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 3px 16px rgba(13,27,62,.35);position:sticky;top:0;z-index:100}}
.hlogo{{width:32px;height:32px;background:var(--teal);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;margin-right:14px}}
header h1{{font-size:14px;font-weight:700;letter-spacing:.6px;text-transform:uppercase}}
header h1 span{{font-weight:400;color:#9ab0cc;font-size:12px;display:block;text-transform:none;letter-spacing:0;margin-top:1px}}
#ts{{font-size:11px;color:#7a99bb;white-space:nowrap}}
.voltar{{font-size:11px;color:#7a99bb;text-decoration:none;display:flex;align-items:center;gap:4px;margin-left:20px;opacity:.8}}
.voltar:hover{{opacity:1}}
.krow{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;padding:18px 28px 4px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:14px 18px;box-shadow:var(--shadow);position:relative;overflow:hidden}}
.kpi::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,var(--teal),var(--teal-light))}}
.kpi.ka::before{{background:linear-gradient(90deg,var(--red),#e74c3c)}}
.kl{{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.kv{{font-size:19px;font-weight:700;letter-spacing:-.5px;line-height:1}}
.kv.neg{{color:var(--red)}}
.ks{{font-size:11px;color:var(--muted);margin-top:5px}}
.fbar{{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end}}
.fg{{display:flex;flex-direction:column;gap:4px}}
.fg label{{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}}
.ac-wrap{{position:relative;min-width:200px}}
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
.btn-r{{background:var(--red);color:#fff}}
.btn-y{{background:#d4820a;color:#fff}}
.mes-badge{{background:var(--navy);color:#c8d8ec;border-radius:6px;padding:7px 14px;font-size:13px;font-weight:600;letter-spacing:.3px;white-space:nowrap}}
.tsec{{padding:16px 28px 32px}}
.thead-row{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}}
.ttitle{{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.sw{{position:relative}}
.sw input{{border:2px solid var(--teal);border-radius:6px;padding:8px 12px;font-size:13px;width:280px;background:#fff;box-shadow:0 0 0 3px rgba(0,144,168,.08)}}
.sw input:focus{{outline:none;border-color:var(--navy)}}
.sw input::placeholder{{color:var(--teal);font-weight:500}}
.sw::before{{content:'';display:none}}
.tw{{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}}
table{{width:100%;border-collapse:collapse;min-width:600px}}
thead th{{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:left;white-space:nowrap;letter-spacing:.3px}}
thead th.right{{text-align:right}}
tbody tr{{transition:background .1s}}
tbody tr:hover td{{background:var(--hover)!important}}
td{{padding:9px 14px;border-bottom:1px solid var(--border);white-space:nowrap;font-variant-numeric:tabular-nums}}
td.right{{text-align:right}}
.vneg{{color:var(--red);font-weight:700}}
.vpos{{color:var(--green);font-weight:700}}
tfoot td{{background:#e8f0f8;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;font-size:12.5px}}
.empty{{text-align:center;padding:56px;color:var(--muted)}}
tr.grp-l1{{background:var(--navy);cursor:pointer}}
tr.grp-l1 td{{color:#e8f0fc;font-weight:700;padding:9px 14px}}
tr.grp-l1:hover td{{background:var(--navy-mid)!important}}
tr.grp-l2 td{{padding:8px 14px 8px 28px;background:var(--surface)}}
tr.grp-l2:nth-child(even) td{{background:var(--row-alt)}}
tr.grp-l2:hover td{{background:var(--hover)!important}}
.tog{{margin-right:6px;font-style:normal;display:inline-block;width:12px}}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">&#127963;</div>
    <h1>Concilia&#231;&#227;o SIGGO e SISGEPAT &#8212; Bens Im&#243;veis
      <span>SIGGO &middot; Ano Exerc&#237;cio {ano}</span>
    </h1>
    <a class="voltar" href="index.html">&#8592; Painel inicial</a>
  </div>
  <span id="ts">Gerado em: {meta['emitido']}</span>
</header>
<div class="krow" id="krow"></div>
<div class="fbar">
  <div class="fg"><label>M&#234;s de Refer&#234;ncia</label>
    <div class="mes-badge" id="mes-ref">—</div>
  </div>
  <div class="fg"><label>Unidade Gestora</label>
    <div class="ac-wrap" style="min-width:280px">
      <input id="fu-input" class="ac-input" type="text" placeholder="C&#243;digo ou nome..." autocomplete="off" oninput="onAC('u')" onfocus="onAC('u')" onblur="offAC('u')">
      <button class="ac-clear" id="fu-clear" onclick="limAC('u')" title="Limpar">&#x2715;</button>
      <div class="ac-dd" id="fu-dd"></div>
    </div>
  </div>
  <div class="fg"><label>Conta Cont&#225;bil</label>
    <div class="ac-wrap" style="min-width:300px">
      <input id="fc-input" class="ac-input" type="text" placeholder="C&#243;digo ou nome..." autocomplete="off" oninput="onAC('c')" onfocus="onAC('c')" onblur="offAC('c')">
      <button class="ac-clear" id="fc-clear" onclick="limAC('c')" title="Limpar">&#x2715;</button>
      <div class="ac-dd" id="fc-dd"></div>
    </div>
  </div>
  <div class="bgrp">
    <button class="btn btn-r" id="btn-dif" onclick="toggleDif()">&#x26A0; Somente Diferen&#231;as</button>
    <button class="btn btn-y" id="btn-conc" onclick="toggleConc()">&#x25CF; Somente contas concili&#225;veis</button>
    <button class="btn btn-g" onclick="expandAll()">&#x25BC; Expandir tudo</button>
    <button class="btn btn-g" onclick="collapseAll()">&#x25B2; Recolher tudo</button>
    <button class="btn btn-g" onclick="limpar()">&#x21BA; Limpar filtros</button>
    <button class="btn btn-p" onclick="exportar()">&#x2B07; Exportar CSV</button>
  </div>
</div>
<div class="tsec">
  <div class="thead-row">
    <span class="ttitle" id="cnt"></span>
    <div class="sw"><input id="busca" type="text" placeholder="Buscar na tabela..." oninput="aplicar()"></div>
  </div>
  <div class="tw"><table>
    <thead><tr>
      <th>Unidade Gestora / Conta Cont&#225;bil</th>
      <th class="right">SISGEPAT</th>
      <th class="right">SIGGO</th>
      <th class="right">Diferen&#231;a</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
    <tfoot id="tfoot"></tfoot>
  </table></div>
</div>
<script>
const B64_META="{b64_meta}";
const B64_QUADRO="{b64_quadro}";
async function decomp(b64){{const bin=atob(b64),buf=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)buf[i]=bin.charCodeAt(i);const ds=new DecompressionStream("gzip"),w=ds.writable.getWriter();w.write(buf);w.close();return JSON.parse(new TextDecoder().decode(await new Response(ds.readable).arrayBuffer()));}}
const brl=v=>isNaN(v)||v==null?'—':Number(v).toLocaleString('pt-BR',{{style:'currency',currency:'BRL'}});
const vcls=v=>Math.abs(v||0)<0.005?brl(0):(v<0?'<span class="vneg">'+brl(v)+'</span>':'<span class="vpos">'+brl(v)+'</span>');
let ALL=[],fil=[];
function buildList(keyFn,labelFn){{const m={{}};ALL.forEach(r=>{{const k=keyFn(r);if(k!=null&&k!==''&&!m[k])m[k]=labelFn(r);}});return Object.entries(m).map(([c,label])=>{{return{{c,label}}}}).sort((a,b)=>a.c.localeCompare(b.c,'pt-BR'));}}
let acState={{}};
function selACI(el){{selAC(el.dataset.k,el.dataset.c,el.dataset.lbl);}}
function renderACDD(k,lista){{const dd=document.getElementById(acState[k].dd);if(!lista.length){{dd.innerHTML='<div class="ac-dd-empty">Nenhum resultado</div>';dd.style.display='block';return;}}dd.innerHTML=lista.slice(0,120).map(x=>{{const sep=x.label.indexOf(' - ');const h=sep>=0?'<strong>'+x.label.slice(0,sep)+'</strong> &mdash; '+x.label.slice(sep+2):x.label;const sl=x.label.replace(/"/g,'&quot;');return '<div class="ac-dd-item" data-k="'+k+'" data-c="'+x.c+'" data-lbl="'+sl+'" onmousedown="selACI(this)">'+h+'</div>';}}).join('');dd.style.display='block';}}
function onAC(k){{const st=acState[k],v=document.getElementById(st.inp).value.toLowerCase();const m=v?st.list.filter(x=>x.c.includes(v)||x.label.toLowerCase().includes(v)):st.list;renderACDD(k,m);}}
function offAC(k){{setTimeout(()=>document.getElementById(acState[k].dd).style.display='none',200);}}
function selAC(k,c,label){{const st=acState[k];st.sel=c;document.getElementById(st.inp).value=label;document.getElementById(st.dd).style.display='none';document.getElementById(st.clr).style.display='block';aplicar();}}
function limAC(k){{const st=acState[k];st.sel='';document.getElementById(st.inp).value='';document.getElementById(st.clr).style.display='none';aplicar();}}
let _somenteDif=false;
let _somenteConciliaveis=false;
const CONTAS_CONC=new Set(['123210800','123210900','123211000','123219000','123219100']);
function aplicar(){{const b=document.getElementById('busca').value.trim().toLowerCase();fil=ALL.filter(r=>{{if(acState['u'].sel&&r.UG!==acState['u'].sel)return false;if(acState['c'].sel&&r.CONTA!==acState['c'].sel)return false;if(_somenteDif&&Math.abs(r.DIF||0)<0.005)return false;if(_somenteConciliaveis&&!CONTAS_CONC.has(r.CONTA))return false;if(b&&!(r.UG_LABEL||'').toLowerCase().includes(b)&&!(r.CONTA_LABEL||'').toLowerCase().includes(b))return false;return true;}});render();}}
function toggleDif(){{_somenteDif=!_somenteDif;const btn=document.getElementById('btn-dif');btn.style.filter=_somenteDif?'brightness(0.75)':'';btn.style.boxShadow=_somenteDif?'inset 0 2px 5px rgba(0,0,0,.35)':'';aplicar();}}
function toggleConc(){{_somenteConciliaveis=!_somenteConciliaveis;const btn=document.getElementById('btn-conc');btn.style.filter=_somenteConciliaveis?'brightness(0.80)':'';btn.style.boxShadow=_somenteConciliaveis?'inset 0 2px 5px rgba(0,0,0,.30)':'';aplicar();}}
function expandAll(){{document.querySelectorAll('tr.grp-l1').forEach(r=>{{const id=r.dataset.id;if(id){{document.querySelectorAll('tr[data-p="'+id+'"]').forEach(c=>c.style.display='');r.querySelector('.tog').innerHTML='▼';}}}}); }}
function collapseAll(){{document.querySelectorAll('tr.grp-l2').forEach(r=>r.style.display='none');document.querySelectorAll('.tog').forEach(t=>t.innerHTML='▶');}}
function limpar(){{document.getElementById('busca').value='';['u','c'].forEach(k=>limAC(k));_somenteDif=false;const b=document.getElementById('btn-dif');b.style.filter='';b.style.boxShadow='';_somenteConciliaveis=false;const bc=document.getElementById('btn-conc');bc.style.filter='';bc.style.boxShadow='';aplicar();}}
function renderKPIs(){{
  const totSIS=fil.reduce((a,r)=>a+(r.SIS||0),0),totSIG=fil.reduce((a,r)=>a+(r.SIG||0),0),totDIF=fil.reduce((a,r)=>a+(r.DIF||0),0);
  const subBase=_somenteConciliaveis?'Adm. Direta + Fundos &middot; contas concili&aacute;veis com SISGEPAT (1232108/09/11/90/91)':'Adm. Direta + Fundos &middot; todas as contas do grupo 1232 (SISGEPAT e demais)';
  const subDif=_somenteConciliaveis?'Diferen&ccedil;a nas 5 contas concili&aacute;veis &mdash; o que deve estar zerado':'Total exibido &mdash; inclui contas sem correspond&ecirc;ncia direta no SISGEPAT';
  document.getElementById('kv-sis').textContent=brl(totSIS);
  document.getElementById('kv-sig').textContent=brl(totSIG);
  document.getElementById('kv-dif').innerHTML=vcls(totDIF);
  document.getElementById('ks-sis').innerHTML=subBase;
  document.getElementById('ks-sig').innerHTML=subBase;
  document.getElementById('ks-dif').innerHTML=subDif;
  document.getElementById('kpi-dif').classList.toggle('ka',Math.abs(totDIF)>=0.01);
}}
function render(){{
  renderKPIs();
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  const totSIS=fil.reduce((a,r)=>a+(r.SIS||0),0),totSIG=fil.reduce((a,r)=>a+(r.SIG||0),0),totDIF=fil.reduce((a,r)=>a+(r.DIF||0),0);
  document.getElementById('cnt').textContent=fil.length.toLocaleString('pt-BR')+' linha'+(fil.length!==1?'s':'');
  if(!fil.length){{tb.innerHTML='<tr><td colspan="4" class="empty">Nenhum registro encontrado.</td></tr>';tf.innerHTML='';return;}}
  const tree={{}};
  fil.forEach(r=>{{
    const u=r.UG,co=r.CONTA;
    if(!tree[u])tree[u]={{sis:0,sig:0,dif:0,lbl:r.UG_LABEL,ch:{{}}}};
    if(!tree[u].ch[co])tree[u].ch[co]={{sis:0,sig:0,dif:0,lbl:r.CONTA_LABEL}};
    tree[u].ch[co].sis+=r.SIS||0;tree[u].ch[co].sig+=r.SIG||0;tree[u].ch[co].dif+=r.DIF||0;
    tree[u].sis+=r.SIS||0;tree[u].sig+=r.SIG||0;tree[u].dif+=r.DIF||0;
  }});
  let html='',idx=0;
  const srt=o=>Object.keys(o).sort((a,b)=>a.localeCompare(b,'pt-BR'));
  srt(tree).forEach(u=>{{const uId='r'+(idx++),uN=tree[u];html+='<tr class="grp-l1" data-id="'+uId+'" onclick="tog(this.dataset.id)"><td><span class="tog">&#9654;</span>'+uN.lbl+'</td><td class="right">'+brl(uN.sis)+'</td><td class="right">'+brl(uN.sig)+'</td><td class="right">'+vcls(uN.dif)+'</td></tr>';srt(uN.ch).forEach(co=>{{const coN=uN.ch[co];html+='<tr class="grp-l2" data-p="'+uId+'" style="display:none"><td>'+coN.lbl+'</td><td class="right">'+brl(coN.sis)+'</td><td class="right">'+brl(coN.sig)+'</td><td class="right">'+vcls(coN.dif)+'</td></tr>';}})}});
  tb.innerHTML=html;
  tf.innerHTML='<tr><td>Total geral ('+fil.length.toLocaleString('pt-BR')+' linhas)</td><td class="right">'+brl(totSIS)+'</td><td class="right">'+brl(totSIG)+'</td><td class="right">'+vcls(totDIF)+'</td></tr>';
}}
function tog(id){{const row=document.querySelector('tr[data-id="'+id+'"]');const togEl=row.querySelector('.tog');const exp=togEl.innerHTML==='&#9660;'||togEl.textContent==='▼';if(exp){{collapseDesc(id);togEl.innerHTML='&#9654;';}}else{{document.querySelectorAll('tr[data-p="'+id+'"]').forEach(r=>{{r.style.display='';const t=r.querySelector('.tog');if(t)t.innerHTML='&#9654;';}});togEl.innerHTML='&#9660;';}}}}
function collapseDesc(id){{document.querySelectorAll('tr[data-p="'+id+'"]').forEach(r=>{{const cid=r.getAttribute('data-id');if(cid)collapseDesc(cid);r.style.display='none';const t=r.querySelector('.tog');if(t)t.innerHTML='&#9654;';}});}}
function exportar(){{if(!fil.length)return alert('Nenhum dado para exportar.');const hdrs=['UG','Unidade Gestora','Conta','Descricao Conta','SISGEPAT','SIGGO','Diferenca'];const keys=['UG','NOME','CONTA','DESC','SIS','SIG','DIF'];const cel=v=>{{if(typeof v==='number')return String(v).replace('.',',');const s=(v||'').toString().trim();return /^\\d+$/.test(s)?'"'+s+'"':s;}};const csv=[hdrs.join(';'),...fil.map(r=>keys.map(k=>cel(r[k])).join(';'))].join('\\n');const a=Object.assign(document.createElement('a'),{{href:URL.createObjectURL(new Blob(['\\uFEFF'+csv],{{type:'text/csv;charset=utf-8'}})),download:'conciliacao_sisgepat.csv'}});a.click();URL.revokeObjectURL(a.href);}}
(async()=>{{
  const [meta,quadro]=await Promise.all([decomp(B64_META),decomp(B64_QUADRO)]);
  quadro.forEach(r=>{{r.UG_LABEL=r.UG+(r.NOME?' - '+r.NOME:'');r.CONTA_LABEL=r.CONTA+(r.DESC?' - '+r.DESC:'');}});
  ALL=quadro;
  document.getElementById('krow').innerHTML=
    '<div class="kpi"><div class="kl">SISGEPAT</div><div class="kv" id="kv-sis">—</div><div class="ks" id="ks-sis"></div></div>'+
    '<div class="kpi"><div class="kl">SIGGO</div><div class="kv" id="kv-sig">—</div><div class="ks" id="ks-sig"></div></div>'+
    '<div class="kpi ka" id="kpi-dif"><div class="kl">Diverg&#234;ncias</div><div class="kv" id="kv-dif">—</div><div class="ks" id="ks-dif"></div></div>';
  document.getElementById('mes-ref').textContent=meta.mes_label+'/'+meta.ano;
  acState={{
    'u':{{sel:'',list:buildList(r=>r.UG,r=>r.UG_LABEL),inp:'fu-input',clr:'fu-clear',dd:'fu-dd'}},
    'c':{{sel:'',list:buildList(r=>r.CONTA,r=>r.CONTA_LABEL),inp:'fc-input',clr:'fc-clear',dd:'fc-dd'}}
  }};
  aplicar();
}})();
window.tog=tog;window.collapseDesc=collapseDesc;
window.onAC=onAC;window.offAC=offAC;window.selAC=selAC;window.limAC=limAC;
window.aplicar=aplicar;window.limpar=limpar;window.exportar=exportar;
window.toggleDif=toggleDif;window.toggleConc=toggleConc;window.expandAll=expandAll;window.collapseAll=collapseAll;window.selACI=selACI;
</script>
</body></html>"""
    return html


def publicar(html, no_push=False):
    caminho = Path(__file__).parent / ARQUIVO_HTML
    caminho.write_text(html, encoding="utf-8")
    print(f"  HTML salvo: {caminho} ({caminho.stat().st_size/1024/1024:.1f} MB)")
    if no_push or os.environ.get("NO_GIT_PUSH") == "1":
        print("  Push ignorado (--no-push ou NO_GIT_PUSH=1).")
        return
    repo_dir = str(Path(__file__).parent)
    url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
    subprocess.run(["git", "-C", repo_dir, "add", ARQUIVO_HTML], check=True)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m",
                    f"auto: atualiza conciliacao_bens_imoveis_sisgepat.html"], check=True)
    subprocess.run(["git", "-C", repo_dir, "push", url,
                    f"HEAD:{GITHUB_BRANCH}"], check=True)


def main():
    hoje = date.today()
    mes_def = hoje.month - 1 if hoje.month > 1 else 12
    ano_def = hoje.year if hoje.month > 1 else hoje.year - 1

    p = argparse.ArgumentParser()
    p.add_argument("--mes",     type=int, default=mes_def)
    p.add_argument("--ano",     type=int, default=ano_def)
    p.add_argument("--no-push", action="store_true")
    a = p.parse_args()

    quadro, eventos, transf, achados, siggo_tomb = extrair(a.mes, a.ano)
    print(f"[{{datetime.now():%H:%M:%S}}] Gerando HTML...")
    html = gerar_html(quadro, eventos, transf, achados, siggo_tomb, a.mes, a.ano)
    publicar(html, a.no_push)
    print(f"  Concluido.\n")

if __name__ == "__main__":
    main()