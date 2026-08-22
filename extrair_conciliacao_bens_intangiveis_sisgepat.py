"""
Conciliação Bens Intangíveis — SIGGO × SISGEPAT (contas 124XXXXXX)
Gera painel HTML autocontido e publica no GitHub Pages.

SISGEPAT: SIGGO.PAT_BENSMOVEIS filtrado por EL_CODIGO='40', agrupado por LOCAL
          DE GUARDA (LO_CODIGO, 3 primeiros dígitos) → COUG via de_para.
          MO_VALORATUAL está em centavos → dividir por 100.
          Mapeamento TP_CODIGO → Conta Contábil SIGGO:
            TP 8  (Desenvolvimento de Sistemas)           → 124110100 SOFTWARES
            TP 25 (Aquisição de Software)                 → 124110200 SOFTWARE EM DESENVOLVIMENTO
            TP 26 (Desenvolvimento de Software/Encomenda) → 124110200 SOFTWARE EM DESENVOLVIMENTO

SIGGO:    MIL2026.VSALDOCONTABIL — contas 124110100 e 124110200.
          VADEBITO - VACREDITO (classe 1 — devedora).
          Restrito às UGs da Administração Direta e Fundos da Direta (de_para).

Uso:
    python extrair_conciliacao_bens_intangiveis_sisgepat.py
    python extrair_conciliacao_bens_intangiveis_sisgepat.py --mes 7 --ano 2026
    python extrair_conciliacao_bens_intangiveis_sisgepat.py --no-push
"""
import argparse, base64, gzip, json, os, subprocess, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from datetime import date, datetime
from pathlib import Path

import oracledb
import pandas as pd
from dotenv import load_dotenv

PASTA        = Path(__file__).parent
load_dotenv(PASTA / ".env")

ORACLE_USER   = os.environ["ORACLE_USER"]
ORACLE_PASS   = os.environ["ORACLE_PASS"]
ORACLE_DSN    = os.environ["ORACLE_DSN"]
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_USER   = os.environ.get("GITHUB_USER", "")
GITHUB_REPO   = os.environ.get("GITHUB_REPO", "")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
ARQUIVO_HTML  = "conciliacao_bens_intangiveis_sisgepat.html"

INSTANT_CLIENT_DIR = r"C:\oracle\instantclient_23_0"

MESES = {1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
         7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}

# ── de_para: LO3 (3 primeiros dígitos de LO_CODIGO) → COUG ──────────────────
DE_PARA_ROWS = [
    ("002","220101"),("003","200101"),("004","210101"),("005","130103"),
    ("006","160101"),("012","230101"),("014","230103"),("019","170101"),
    ("021","190101"),("022","190103"),("023","190104"),("024","190105"),
    ("025","190106"),("026","190107"),("027","190108"),("028","190109"),
    ("029","190110"),("030","190111"),("031","190112"),("032","190113"),
    ("033","190114"),("034","120101"),("035","020101"),("036","220103"),
    ("037","220104"),("041","190115"),("042","190116"),("043","190117"),
    ("045","150106"),("047","190118"),("048","190119"),("049","190120"),
    ("050","190121"),("051","220105"),("052","280101"),("064","190122"),
    ("065","190123"),("066","190124"),("067","190125"),("069","190128"),
    ("074","190127"),("075","190126"),("076","190129"),("078","190131"),
    ("079","190130"),("081","440101"),("083","180101"),("086","190132"),
    ("089","480101"),("091","450101"),("093","150101"),("099","090101"),
    ("102","190133"),("107","340101"),("109","220202"),("110","260101"),
    ("112","440202"),("113","200203"),("114","170203"),("116","100101"),
    ("119","630101"),("122","150201"),("123","320201"),("124","150204"),
    ("125","570101"),("126","640101"),("127","250101"),("128","650101"),
    ("129","310101"),("130","190134"),("132","610101"),("133","110101"),
    ("134","240204"),("135","180203"),("136","190135"),("137","140202"),
    ("138","150205"),("140","190137"),("141","190136"),("142","190219"),
    ("143","700101"),  # SGDI — Secretaria de Governança Digital e Integração (abr/2026)
]

def _de_para_sql():
    linhas = [f"  SELECT '{lo3}' LO3, '{coug}' COUG FROM dual" for lo3, coug in DE_PARA_ROWS]
    return "WITH de_para AS (\n" + " UNION ALL\n".join(linhas) + "\n)"

# ── SQL ───────────────────────────────────────────────────────────────────────
# Mapeamento TP_CODIGO → conta contábil SIGGO aplicado no SISGEPAT.
# Join feito por COUG + CONTA para que SIGGO e SISGEPAT se conciliem
# conta a conta (124110100 vs 124110200).
SQL = """{de_para},
cougs_direta AS (
    SELECT TO_NUMBER(COUG) COUG_NUM FROM de_para
),
contas_info AS (
    SELECT TO_CHAR(COCONTACONTABIL) AS CONTA, TRIM(NOCONTACONTABIL) AS NOCONTA
    FROM MIL2026.VCONTACONTABIL
    WHERE COCONTACONTABIL IN (124110100, 124110200)
),
sisgepat AS (
    SELECT
        LPAD(dp.COUG, 6, '0') AS COUG,
        CASE TO_NUMBER(bm.TP_CODIGO)
            WHEN 8  THEN '124110100'
            WHEN 25 THEN '124110200'
            WHEN 26 THEN '124110200'
        END AS CONTA,
        ROUND(SUM(bm.MO_VALORATUAL) / 100, 2) AS SALDO_SISGEPAT
    FROM SIGGO.PAT_BENSMOVEIS bm
    JOIN de_para dp ON SUBSTR(bm.LO_CODIGO, 1, 3) = dp.LO3
    WHERE bm.EL_CODIGO = '40'
      AND TO_NUMBER(bm.TP_CODIGO) IN (8, 25, 26)
      AND bm.MO_DTINCORPORACAO IS NOT NULL
      AND bm.MO_DTINCORPORACAO < :data_corte
      AND bm.MO_DTAQUISICAO IS NOT NULL
      AND bm.MO_DTAQUISICAO < :data_corte
    GROUP BY dp.COUG,
        CASE TO_NUMBER(bm.TP_CODIGO)
            WHEN 8  THEN '124110100'
            WHEN 25 THEN '124110200'
            WHEN 26 THEN '124110200'
        END
),
siggo AS (
    SELECT
        LPAD(TO_CHAR(v.COUG), 6, '0')  AS COUG,
        TO_CHAR(v.COCONTACONTABIL)      AS CONTA,
        ROUND(SUM(v.VADEBITO - v.VACREDITO), 2) AS TOTAL_SIGGO
    FROM MIL2026.VSALDOCONTABIL v
    WHERE v.INMES <= :mes
      AND v.COCONTACONTABIL IN (124110100, 124110200)
      AND v.COUG IN (SELECT COUG_NUM FROM cougs_direta)
    GROUP BY v.COUG, v.COCONTACONTABIL
)
SELECT
    NVL(si.COUG,  sg.COUG)  AS COUG,
    TRIM(ug.NOUG)            AS NOUG,
    NVL(si.CONTA, sg.CONTA)  AS CONTA,
    ci.NOCONTA               AS NOCONTA,
    NVL(sg.TOTAL_SIGGO,  0)  AS TOTAL_SIGGO,
    NVL(si.SALDO_SISGEPAT, 0) AS SALDO_SISGEPAT,
    ROUND(NVL(sg.TOTAL_SIGGO, 0) - NVL(si.SALDO_SISGEPAT, 0), 2) AS DIFERENCA
FROM sisgepat si
FULL OUTER JOIN siggo sg
    ON si.COUG = sg.COUG AND si.CONTA = sg.CONTA
LEFT JOIN MIL2026.VUNIDADEGESTORA ug
    ON ug.COUG = TO_NUMBER(NVL(si.COUG, sg.COUG))
LEFT JOIN contas_info ci
    ON ci.CONTA = NVL(si.CONTA, sg.CONTA)
ORDER BY NVL(si.COUG, sg.COUG), NVL(si.CONTA, sg.CONTA)
"""


# ── Extração ──────────────────────────────────────────────────────────────────

def mes_anterior():
    hoje = date.today()
    if hoje.month == 1:
        return 12, hoje.year - 1
    return hoje.month - 1, hoje.year


def extrair(mes, ano):
    if mes == 12:
        data_corte = date(ano + 1, 1, 1)
    else:
        data_corte = date(ano, mes + 1, 1)

    print(f"[{datetime.now():%H:%M:%S}] Conectando ao Oracle...")
    oracledb.init_oracle_client(lib_dir=INSTANT_CLIENT_DIR)
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN)

    sql_final = SQL.format(de_para=_de_para_sql())
    print(f"[{datetime.now():%H:%M:%S}] Executando consulta ({MESES[mes]}/{ano}, corte {data_corte})...")
    df = pd.read_sql(sql_final, conn, params={"mes": mes, "data_corte": data_corte})
    conn.close()

    for col in ["TOTAL_SIGGO", "SALDO_SISGEPAT", "DIFERENCA"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).round(2)

    df["COUG"]    = df["COUG"].astype(str).str.zfill(6)
    df["CONTA"]   = df["CONTA"].astype(str)
    df["NOUG"]    = df["NOUG"].fillna("(sem nome)")
    df["NOCONTA"] = df["NOCONTA"].fillna("")

    # Remove linhas em que SIGGO e SISGEPAT são ambos zero
    df = df[(df["TOTAL_SIGGO"].abs() > 0.005) | (df["SALDO_SISGEPAT"].abs() > 0.005)]

    print(f"  {len(df)} linhas retornadas.")
    total_siggo  = df["TOTAL_SIGGO"].sum()
    total_sispat = df["SALDO_SISGEPAT"].sum()
    n_diff       = (df["DIFERENCA"].abs() > 0.005).sum()
    print(f"  Total SIGGO:    R$ {total_siggo:>18,.2f}")
    print(f"  Total SISGEPAT: R$ {total_sispat:>18,.2f}")
    print(f"  Diferença:      R$ {total_siggo - total_sispat:>18,.2f}")
    print(f"  Linhas com diferença: {n_diff}")

    return df, mes, ano, data_corte


# ── HTML ──────────────────────────────────────────────────────────────────────

def gerar_html(df, mes, ano, data_corte):
    registros = []
    for r in df.itertuples(index=False):
        registros.append({
            "COUG":           str(r.COUG),
            "NOUG":           str(r.NOUG),
            "CONTA":          str(r.CONTA),
            "NOCONTA":        str(r.NOCONTA),
            "TOTAL_SIGGO":    float(r.TOTAL_SIGGO),
            "SALDO_SISGEPAT": float(r.SALDO_SISGEPAT),
            "DIFERENCA":      float(r.DIFERENCA),
        })

    meta = {
        "mes_label":  MESES[mes],
        "mes_num":    mes,
        "ano":        ano,
        "gerado_em":  datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "data_corte": data_corte.strftime("%d/%m/%Y"),
    }

    def b64gz(obj):
        return base64.b64encode(
            gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",",":")).encode("utf-8"), compresslevel=9)
        ).decode()

    b64_meta   = b64gz(meta)
    b64_quadro = b64gz(registros)

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Conciliação SIGGO e SISGEPAT - Bens Intangíveis</title>
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
.mes-badge{{background:var(--navy);color:#c8d8ec;border-radius:6px;padding:7px 14px;font-size:13px;font-weight:600;letter-spacing:.3px;white-space:nowrap}}
.tsec{{padding:16px 28px 32px}}
.thead-row{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px}}
.ttitle{{font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}}
.sw{{position:relative}}
.sw input{{border:2px solid var(--teal);border-radius:6px;padding:8px 12px;font-size:13px;width:280px;background:#fff;box-shadow:0 0 0 3px rgba(0,144,168,.08)}}
.sw input:focus{{outline:none;border-color:var(--navy)}}
.sw input::placeholder{{color:var(--teal);font-weight:500}}
.tw{{border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;box-shadow:var(--shadow);overflow-x:auto}}
table{{width:100%;border-collapse:collapse;min-width:600px}}
thead th{{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:left;white-space:nowrap;letter-spacing:.3px;vertical-align:bottom}}
thead th.right{{text-align:right}}
thead th .sub{{display:block;font-size:9px;font-weight:400;opacity:.7;margin-top:2px;letter-spacing:0}}
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
    <div class="hlogo">&#x1F4BB;</div>
    <h1>Concilia&#231;&#227;o SIGGO e SISGEPAT &#8212; Bens Intang&#237;veis
      <span>SIGGO &middot; Ano Exerc&#237;cio 2026</span>
    </h1>
    <a class="voltar" href="index.html">&#8592; Painel inicial</a>
  </div>
  <span id="ts">Gerado em: —</span>
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
      <th class="right">SIGGO<span class="sub">124XXXXXX</span></th>
      <th class="right">SISGEPAT<span class="sub">Elemento de Despesa 40</span></th>
      <th class="right">Diverg&#234;ncia</th>
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
function aplicar(){{const b=document.getElementById('busca').value.trim().toLowerCase();fil=ALL.filter(r=>{{if(acState['u'].sel&&r.COUG!==acState['u'].sel)return false;if(acState['c'].sel&&r.CONTA!==acState['c'].sel)return false;if(_somenteDif&&Math.abs(r.DIFERENCA||0)<0.005)return false;if(b&&!(r.UG_LABEL||'').toLowerCase().includes(b)&&!(r.CONTA_LABEL||'').toLowerCase().includes(b))return false;return true;}});render();}}
function toggleDif(){{_somenteDif=!_somenteDif;const btn=document.getElementById('btn-dif');btn.style.filter=_somenteDif?'brightness(0.75)':'';btn.style.boxShadow=_somenteDif?'inset 0 2px 5px rgba(0,0,0,.35)':'';aplicar();}}
function expandAll(){{document.querySelectorAll('tr.grp-l1').forEach(r=>{{const id=r.dataset.id;if(id){{document.querySelectorAll('tr[data-p="'+id+'"]').forEach(c=>c.style.display='');r.querySelector('.tog').innerHTML='&#9660;';}}}});}}
function collapseAll(){{document.querySelectorAll('tr.grp-l2').forEach(r=>r.style.display='none');document.querySelectorAll('.tog').forEach(t=>t.innerHTML='&#9654;');}}
function limpar(){{document.getElementById('busca').value='';['u','c'].forEach(k=>limAC(k));_somenteDif=false;const b=document.getElementById('btn-dif');b.style.filter='';b.style.boxShadow='';aplicar();}}
function renderKPIs(){{
  const totSIG=fil.reduce((a,r)=>a+(r.TOTAL_SIGGO||0),0);
  const totSIS=fil.reduce((a,r)=>a+(r.SALDO_SISGEPAT||0),0);
  const totDIF=fil.reduce((a,r)=>a+(r.DIFERENCA||0),0);
  document.getElementById('kv-sig').textContent=brl(totSIG);
  document.getElementById('kv-sis').textContent=brl(totSIS);
  document.getElementById('kv-dif').innerHTML=vcls(totDIF);
  document.getElementById('ks-sig').innerHTML='Contas cont&aacute;beis 124XXXXXX &mdash; Bens Intang&iacute;veis';
  document.getElementById('ks-sis').innerHTML='Elemento de Despesa 40 &mdash; Servi&ccedil;o de Tecnologia da Informa&ccedil;&atilde;o e Comunica&ccedil;&atilde;o';
  document.getElementById('ks-dif').innerHTML='SIGGO &minus; SISGEPAT &mdash; diferen&ccedil;a a ser analisada e regularizada';
  document.getElementById('kpi-dif').classList.toggle('ka',Math.abs(totDIF)>=0.01);
}}
function render(){{
  renderKPIs();
  const tb=document.getElementById('tbody'),tf=document.getElementById('tfoot');
  const totSIG=fil.reduce((a,r)=>a+(r.TOTAL_SIGGO||0),0);
  const totSIS=fil.reduce((a,r)=>a+(r.SALDO_SISGEPAT||0),0);
  const totDIF=fil.reduce((a,r)=>a+(r.DIFERENCA||0),0);
  document.getElementById('cnt').textContent=fil.length.toLocaleString('pt-BR')+' linha'+(fil.length!==1?'s':'');
  if(!fil.length){{tb.innerHTML='<tr><td colspan="4" class="empty">Nenhum registro encontrado.</td></tr>';tf.innerHTML='';return;}}
  const tree={{}};
  fil.forEach(r=>{{
    const u=r.COUG,co=r.CONTA;
    if(!tree[u])tree[u]={{sig:0,sis:0,dif:0,lbl:r.UG_LABEL,ch:{{}}}};
    if(!tree[u].ch[co])tree[u].ch[co]={{sig:0,sis:0,dif:0,lbl:r.CONTA_LABEL}};
    tree[u].ch[co].sig+=r.TOTAL_SIGGO||0;tree[u].ch[co].sis+=r.SALDO_SISGEPAT||0;tree[u].ch[co].dif+=r.DIFERENCA||0;
    tree[u].sig+=r.TOTAL_SIGGO||0;tree[u].sis+=r.SALDO_SISGEPAT||0;tree[u].dif+=r.DIFERENCA||0;
  }});
  let html='',idx=0;
  const srt=o=>Object.keys(o).sort((a,b)=>a.localeCompare(b,'pt-BR'));
  srt(tree).forEach(u=>{{const uid='r'+(idx++),uN=tree[u];
    html+='<tr class="grp-l1" data-id="'+uid+'" onclick="tog(this.dataset.id)"><td><span class="tog">&#9654;</span>'+uN.lbl+'</td><td class="right">'+brl(uN.sig)+'</td><td class="right">'+brl(uN.sis)+'</td><td class="right">'+vcls(uN.dif)+'</td></tr>';
    srt(uN.ch).forEach(co=>{{const coN=uN.ch[co];
      html+='<tr class="grp-l2" data-p="'+uid+'" style="display:none"><td>'+coN.lbl+'</td><td class="right">'+brl(coN.sig)+'</td><td class="right">'+brl(coN.sis)+'</td><td class="right">'+vcls(coN.dif)+'</td></tr>';
    }});
  }});
  tb.innerHTML=html;
  tf.innerHTML='<tr><td>Total geral ('+fil.length.toLocaleString('pt-BR')+' linhas)</td><td class="right">'+brl(totSIG)+'</td><td class="right">'+brl(totSIS)+'</td><td class="right">'+vcls(totDIF)+'</td></tr>';
}}
function tog(id){{const row=document.querySelector('tr[data-id="'+id+'"]');const togEl=row.querySelector('.tog');const exp=togEl.innerHTML==='&#9660;'||togEl.textContent==='▼';if(exp){{collapseDesc(id);togEl.innerHTML='&#9654;';}}else{{document.querySelectorAll('tr[data-p="'+id+'"]').forEach(r=>{{r.style.display='';const t=r.querySelector('.tog');if(t)t.innerHTML='&#9654;';}});togEl.innerHTML='&#9660;';}}}}
function collapseDesc(id){{document.querySelectorAll('tr[data-p="'+id+'"]').forEach(r=>{{const cid=r.getAttribute('data-id');if(cid)collapseDesc(cid);r.style.display='none';const t=r.querySelector('.tog');if(t)t.innerHTML='&#9654;';}});}}
function exportar(){{if(!fil.length)return alert('Nenhum dado para exportar.');const hdrs=['COUG','Unidade Gestora','Conta','Nome Conta','SIGGO','SISGEPAT','Divergencia'];const keys=['COUG','NOUG','CONTA','NOCONTA','TOTAL_SIGGO','SALDO_SISGEPAT','DIFERENCA'];const cel=v=>{{if(typeof v==='number')return String(v).replace('.',',');const s=(v||'').toString().trim();return /^\\d+$/.test(s)?'"'+s+'"':s;}};const csv=[hdrs.join(';'),...fil.map(r=>keys.map(k=>cel(r[k])).join(';'))].join('\\n');const a=Object.assign(document.createElement('a'),{{href:URL.createObjectURL(new Blob(['\\uFEFF'+csv],{{type:'text/csv;charset=utf-8'}})),download:'conciliacao_bens_intangiveis_sisgepat.csv'}});a.click();URL.revokeObjectURL(a.href);}}
(async()=>{{
  const [meta,quadro]=await Promise.all([decomp(B64_META),decomp(B64_QUADRO)]);
  quadro.forEach(r=>{{r.UG_LABEL=r.COUG+(r.NOUG?' - '+r.NOUG:'');r.CONTA_LABEL=r.CONTA+(r.NOCONTA?' - '+r.NOCONTA:'');}});
  ALL=quadro;
  document.getElementById('ts').textContent='Gerado em: '+meta.gerado_em;
  document.getElementById('krow').innerHTML=
    '<div class="kpi"><div class="kl">SIGGO</div><div class="kv" id="kv-sig">—</div><div class="ks" id="ks-sig"></div></div>'+
    '<div class="kpi"><div class="kl">SISGEPAT</div><div class="kv" id="kv-sis">—</div><div class="ks" id="ks-sis"></div></div>'+
    '<div class="kpi ka" id="kpi-dif"><div class="kl">Diverg&#234;ncias</div><div class="kv" id="kv-dif">—</div><div class="ks" id="ks-dif"></div></div>';
  document.getElementById('mes-ref').textContent=meta.mes_label+'/'+meta.ano;
  acState={{
    'u':{{sel:'',list:buildList(r=>r.COUG,r=>r.UG_LABEL),inp:'fu-input',clr:'fu-clear',dd:'fu-dd'}},
    'c':{{sel:'',list:buildList(r=>r.CONTA,r=>r.CONTA_LABEL),inp:'fc-input',clr:'fc-clear',dd:'fc-dd'}}
  }};
  aplicar();
}})();
window.tog=tog;window.collapseDesc=collapseDesc;
window.onAC=onAC;window.offAC=offAC;window.selAC=selAC;window.limAC=limAC;window.selACI=selACI;
window.aplicar=aplicar;window.limpar=limpar;window.exportar=exportar;
window.toggleDif=toggleDif;window.expandAll=expandAll;window.collapseAll=collapseAll;
</script>
</body></html>"""
    return html


# ── Publicação ────────────────────────────────────────────────────────────────

def publicar(html, no_push):
    dest = PASTA / ARQUIVO_HTML
    dest.write_text(html, encoding="utf-8")
    print(f"  HTML salvo: {dest}")

    if no_push or os.environ.get("NO_GIT_PUSH") == "1":
        print("  Push ignorado (--no-push ou NO_GIT_PUSH=1).")
        return

    subprocess.run(["git", "-C", str(PASTA), "add", ARQUIVO_HTML], check=True)
    subprocess.run(["git", "-C", str(PASTA), "commit", "-m",
                    f"auto: atualiza conciliacao_bens_intangiveis_sisgepat.html"], check=True)
    subprocess.run(["git", "-C", str(PASTA), "push", "origin", GITHUB_BRANCH], check=True)
    print(f"  Publicado: https://{GITHUB_USER}.github.io/{GITHUB_REPO}/{ARQUIVO_HTML}")


# ── Principal ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    mes_def, ano_def = mes_anterior()
    ap.add_argument("--mes",     type=int, default=mes_def)
    ap.add_argument("--ano",     type=int, default=ano_def)
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()

    print(f"[{datetime.now():%H:%M:%S}] Conciliação Bens Intangíveis SIGGO × SISGEPAT ({MESES[a.mes]}/{a.ano})...")
    df, mes, ano, data_corte = extrair(a.mes, a.ano)
    print(f"[{datetime.now():%H:%M:%S}] Gerando HTML...")
    html = gerar_html(df, mes, ano, data_corte)
    publicar(html, a.no_push)
    print(f"[{datetime.now():%H:%M:%S}] Concluído.")

if __name__ == "__main__":
    main()
