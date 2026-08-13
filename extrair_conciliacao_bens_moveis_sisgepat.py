"""
Conciliação Bens Móveis — SIGGO × SISGEPAT (contas 1231XXXXX)
Gera painel HTML autocontido e publica no GitHub Pages.

SISGEPAT: SIGGO.PAT_BENSMOVEIS agrupado por LOCAL DE GUARDA (LO_CODIGO, 3 primeiros
          dígitos) convertido para COUG via de_para — mesma metodologia do colega.
          MO_VALORATUAL está em centavos → dividir por 100.
SIGGO:    MIL2026.VSALDOCONTABIL — contas 1231101XX / 1231108XX / 1231118XX.
Ligação:  COUG + SUBITEM (últimos 2 dígitos da conta = TP_CODIGO do SISGEPAT).

de_para (LO3 → COUG): 75 entradas originais do colega + '143'→'700101' (SGDI,
criada em abr/2026).

Uso:
    python extrair_conciliacao_bens_moveis_sisgepat.py
    python extrair_conciliacao_bens_moveis_sisgepat.py --mes 7 --ano 2026
    python extrair_conciliacao_bens_moveis_sisgepat.py --no-push
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
ARQUIVO_HTML  = "conciliacao_bens_moveis_sisgepat.html"

INSTANT_CLIENT_DIR = r"C:\oracle\instantclient_23_0"

MESES = {1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
         7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}

# ── de_para: LO3 (3 primeiros dígitos de LO_CODIGO) → COUG ───────────────────
# Fonte: mapeamento do colega (75 entradas) + '143'→'700101' (SGDI, abr/2026)
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
    """Gera o bloco CTE de_para para inline no SQL Oracle."""
    linhas = [f"  SELECT '{lo3}' LO3, '{coug}' COUG FROM dual" for lo3, coug in DE_PARA_ROWS]
    return "WITH de_para AS (\n" + " UNION ALL\n".join(linhas) + "\n)"

# ── SQL ───────────────────────────────────────────────────────────────────────

SQL = """{de_para},
sisgepat AS (
    -- Guarda física (LO_CODIGO) → COUG via de_para; MO_VALORATUAL em centavos ÷ 100
    SELECT
        LPAD(dp.COUG, 6, '0')                 AS COUG,
        LPAD(bm.TP_CODIGO, 2, '0')            AS SUBITEM,
        MIN(bm.TP_DESCRICAO)                  AS DESC_SUBITEM,
        ROUND(SUM(bm.MO_VALORATUAL) / 100, 2) AS SALDO_SISGEPAT
    FROM SIGGO.PAT_BENSMOVEIS bm
    JOIN de_para dp ON SUBSTR(bm.LO_CODIGO, 1, 3) = dp.LO3
    WHERE bm.MO_DTINCORPORACAO IS NOT NULL
      AND bm.MO_DTINCORPORACAO < :data_corte
      AND bm.MO_DTAQUISICAO IS NOT NULL
      AND bm.MO_DTAQUISICAO < :data_corte
    GROUP BY dp.COUG, bm.TP_CODIGO
),
siggo AS (
    SELECT
        LPAD(TO_CHAR(v.COUG), 6, '0')                          AS COUG,
        LPAD(SUBSTR(TO_CHAR(v.COCONTACONTABIL), -2), 2, '0')   AS SUBITEM,
        ROUND(SUM(CASE
            WHEN v.COCONTACONTABIL BETWEEN 123110100 AND 123110199
            THEN v.VADEBITO - v.VACREDITO ELSE 0 END), 2)      AS BENS_MOVEIS,
        ROUND(SUM(CASE
            WHEN v.COCONTACONTABIL BETWEEN 123110800 AND 123110899
            THEN v.VADEBITO - v.VACREDITO ELSE 0 END), 2)      AS BENS_MOVEIS_ALMOX,
        ROUND(SUM(CASE
            WHEN v.COCONTACONTABIL BETWEEN 123111800 AND 123111899
            THEN v.VADEBITO - v.VACREDITO ELSE 0 END), 2)      AS BENS_MOVEIS_IMPORT
    FROM MIL2026.VSALDOCONTABIL v
    WHERE v.INMES <= :mes
      AND (  v.COCONTACONTABIL BETWEEN 123110100 AND 123110199
          OR v.COCONTACONTABIL BETWEEN 123110800 AND 123110899
          OR v.COCONTACONTABIL BETWEEN 123111800 AND 123111899)
    GROUP BY v.COUG, SUBSTR(TO_CHAR(v.COCONTACONTABIL), -2)
)
SELECT
    NVL(si.COUG,         sg.COUG)    AS COUG,
    TRIM(ug.NOUG)                    AS NOUG,
    NVL(si.SUBITEM,      sg.SUBITEM) AS SUBITEM,
    NVL(si.DESC_SUBITEM, '')         AS DESC_SUBITEM,
    NVL(sg.BENS_MOVEIS,       0)     AS BENS_MOVEIS,
    NVL(sg.BENS_MOVEIS_ALMOX, 0)     AS BENS_MOVEIS_ALMOX,
    NVL(sg.BENS_MOVEIS_IMPORT,0)     AS BENS_MOVEIS_IMPORT,
    ROUND(
        NVL(sg.BENS_MOVEIS,0) + NVL(sg.BENS_MOVEIS_ALMOX,0) + NVL(sg.BENS_MOVEIS_IMPORT,0)
    , 2)                              AS TOTAL_SIGGO,
    NVL(si.SALDO_SISGEPAT, 0)        AS SALDO_SISGEPAT,
    ROUND(
        NVL(sg.BENS_MOVEIS,0) + NVL(sg.BENS_MOVEIS_ALMOX,0) + NVL(sg.BENS_MOVEIS_IMPORT,0)
        - NVL(si.SALDO_SISGEPAT, 0)
    , 2)                              AS DIFERENCA
FROM sisgepat si
FULL OUTER JOIN siggo sg
    ON si.COUG = sg.COUG AND si.SUBITEM = sg.SUBITEM
LEFT JOIN MIL2026.VUNIDADEGESTORA ug
    ON ug.COUG = TO_NUMBER(NVL(si.COUG, sg.COUG))
ORDER BY NVL(si.COUG, sg.COUG), NVL(si.SUBITEM, sg.SUBITEM)
"""


# ── Extração ──────────────────────────────────────────────────────────────────

def extrair(mes, ano):
    # data_corte: primeiro dia do mês seguinte (exclusive)
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

    # normaliza tipos
    for col in ["BENS_MOVEIS", "BENS_MOVEIS_ALMOX", "BENS_MOVEIS_IMPORT",
                "TOTAL_SIGGO", "SALDO_SISGEPAT", "DIFERENCA"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).round(2)

    df["COUG"]        = df["COUG"].astype(str).str.zfill(6)
    df["SUBITEM"]     = df["SUBITEM"].astype(str).str.zfill(2)
    df["NOUG"]        = df["NOUG"].fillna("(sem nome)")
    df["DESC_SUBITEM"]= df["DESC_SUBITEM"].fillna("")

    print(f"  {len(df)} linhas retornadas.")
    n_diff = (df["DIFERENCA"].abs() > 0.005).sum()
    total_siggo = df["TOTAL_SIGGO"].sum()
    total_sispat= df["SALDO_SISGEPAT"].sum()
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
            "COUG":             str(r.COUG),
            "NOUG":             str(r.NOUG),
            "SUBITEM":          str(r.SUBITEM),
            "DESC_SUBITEM":     str(r.DESC_SUBITEM),
            "BENS_MOVEIS":      float(r.BENS_MOVEIS),
            "BENS_MOVEIS_ALMOX":float(r.BENS_MOVEIS_ALMOX),
            "BENS_MOVEIS_IMPORT":float(r.BENS_MOVEIS_IMPORT),
            "TOTAL_SIGGO":      float(r.TOTAL_SIGGO),
            "SALDO_SISGEPAT":   float(r.SALDO_SISGEPAT),
            "DIFERENCA":        float(r.DIFERENCA),
        })

    json_str  = json.dumps(registros, ensure_ascii=False, separators=(",", ":"))
    dados_b64 = base64.b64encode(
        gzip.compress(json_str.encode("utf-8"), compresslevel=9)
    ).decode()

    mes_nome  = MESES[mes]
    gerado_em = datetime.now().strftime("%d/%m/%Y %H:%M")
    n_linhas  = len(registros)
    total_siggo  = df["TOTAL_SIGGO"].sum()
    total_sispat = df["SALDO_SISGEPAT"].sum()
    diferenca_tot= total_siggo - total_sispat
    n_ug_diff    = df[df["DIFERENCA"].abs() > 0.005]["COUG"].nunique()
    n_diff       = (df["DIFERENCA"].abs() > 0.005).sum()

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Conciliação Bens Móveis SIGGO × SISGEPAT — {mes_nome}/{ano}</title>
<style>
:root{{
  --bg:#f4f6fb;--card:#fff;--hdr:#0d1b3e;--hdr2:#1a3a6e;--acc:#1976d2;
  --pos:#1b5e20;--neg:#b71c1c;--warn:#e65100;--border:#dde3ef;
  --txt:#1a2033;--sub:#5a6480;--radius:10px;--shadow:0 2px 10px #0001;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:var(--bg);color:var(--txt);font-size:13px}}
header{{background:linear-gradient(135deg,var(--hdr),var(--hdr2));color:#fff;padding:22px 28px 18px}}
header h1{{font-size:1.25rem;font-weight:700;margin-bottom:4px}}
header p{{font-size:.82rem;opacity:.8}}
.container{{max-width:1400px;margin:0 auto;padding:20px 16px}}

/* KPIs */
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:20px}}
.kpi{{background:var(--card);border-radius:var(--radius);padding:16px 18px;
      box-shadow:var(--shadow);border-left:4px solid var(--acc)}}
.kpi.neg{{border-left-color:var(--neg)}}
.kpi.pos{{border-left-color:var(--pos)}}
.kpi.warn{{border-left-color:var(--warn)}}
.kpi-label{{font-size:.72rem;color:var(--sub);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.kpi-value{{font-size:1.15rem;font-weight:700}}

/* Filtros */
.filtros{{background:var(--card);border-radius:var(--radius);padding:14px 18px;
          box-shadow:var(--shadow);margin-bottom:16px;display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end}}
.filtros label{{font-size:.75rem;color:var(--sub);display:flex;flex-direction:column;gap:4px}}
.filtros input,.filtros select{{padding:6px 10px;border:1px solid var(--border);border-radius:6px;
    font-size:12px;color:var(--txt);background:#fff;min-width:160px}}
.btn{{padding:7px 16px;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600}}
.btn-pri{{background:var(--acc);color:#fff}}
.btn-sec{{background:#e3eaf5;color:var(--acc)}}

/* Tabela */
.tbl-wrap{{overflow-x:auto;border-radius:var(--radius);box-shadow:var(--shadow)}}
table{{width:100%;border-collapse:collapse;table-layout:fixed;background:var(--card)}}
thead tr th{{background:var(--hdr);color:#fff;padding:9px 8px;text-align:right;
             font-size:.72rem;font-weight:600;white-space:nowrap;cursor:pointer;user-select:none}}
thead tr th.left{{text-align:left}}
thead tr th:hover{{background:var(--hdr2)}}
tbody tr{{border-bottom:1px solid var(--border)}}
tbody tr:hover{{background:#f0f5ff}}
td{{padding:7px 8px;text-align:right;font-size:.8rem}}
td.left{{text-align:left}}
td.diff-pos{{color:var(--pos);font-weight:600}}
td.diff-neg{{color:var(--neg);font-weight:600}}
td.diff-zero{{color:var(--sub)}}

/* Linha de subtotal UG */
tr.ug-sub{{background:#e8edf8!important;font-weight:600}}
tr.ug-sub td{{border-top:2px solid var(--acc)}}

.si{{margin-left:4px;font-size:.65rem;opacity:.7}}
.badge{{display:inline-block;padding:2px 7px;border-radius:10px;font-size:.7rem;font-weight:600}}
.badge-diff{{background:#fde8e8;color:var(--neg)}}
.badge-ok{{background:#e8f5e9;color:var(--pos)}}
tfoot td{{font-weight:700;background:#e8edf8;border-top:2px solid var(--hdr)}}
.info{{font-size:.75rem;color:var(--sub);margin:10px 0 14px}}
</style>
</head>
<body>
<header>
  <h1>Conciliação de Bens Móveis — SIGGO × SISGEPAT</h1>
  <p>{mes_nome}/{ano} &nbsp;·&nbsp; data de corte {data_corte.strftime('%d/%m/%Y')} &nbsp;·&nbsp; gerado em {gerado_em}</p>
</header>
<div class="container">

<div class="kpis" id="kpis">
  <div class="kpi"><div class="kpi-label">Total SIGGO (1231XXXXX)</div><div class="kpi-value" id="kv-siggo">—</div></div>
  <div class="kpi"><div class="kpi-label">Total SISGEPAT</div><div class="kpi-value" id="kv-sispat">—</div></div>
  <div class="kpi neg"><div class="kpi-label">Diferença (SIGGO − SISGEPAT)</div><div class="kpi-value" id="kv-diff">—</div></div>
  <div class="kpi warn"><div class="kpi-label">UGs com diferença</div><div class="kpi-value" id="kv-ugs">—</div></div>
  <div class="kpi warn"><div class="kpi-label">Linhas com diferença</div><div class="kpi-value" id="kv-linhas">—</div></div>
</div>

<div class="filtros">
  <label>UG (código ou nome)<input type="text" id="f-ug" placeholder="ex: 160101 ou Educação" oninput="render()"></label>
  <label>Subitem<input type="text" id="f-sub" placeholder="ex: 35 ou microinformática" oninput="render()"></label>
  <label>Exibir
    <select id="f-mode" onchange="render()">
      <option value="all">Todos</option>
      <option value="diff">Apenas com diferença</option>
      <option value="only-siggo">Apenas no SIGGO (sem SISGEPAT)</option>
      <option value="only-sispat">Apenas no SISGEPAT (sem SIGGO)</option>
    </select>
  </label>
  <label>Agrupar por UG
    <select id="f-grp" onchange="render()">
      <option value="1">Sim</option>
      <option value="0">Não</option>
    </select>
  </label>
  <button class="btn btn-sec" onclick="limparFiltros()">Limpar filtros</button>
</div>

<p class="info" id="info-linhas"></p>

<div class="tbl-wrap">
<table id="tbl">
<thead><tr>
  <th class="left" style="width:110px" onclick="sort('COUG')">UG<span class="si" id="s_COUG">⇅</span></th>
  <th class="left" style="width:200px" onclick="sort('NOUG')">Nome UG<span class="si" id="s_NOUG">⇅</span></th>
  <th class="left" style="width:40px" onclick="sort('SUBITEM')">Sub<span class="si" id="s_SUBITEM">⇅</span></th>
  <th class="left" style="width:170px" onclick="sort('DESC_SUBITEM')">Descrição<span class="si" id="s_DESC_SUBITEM">⇅</span></th>
  <th style="width:130px" onclick="sort('BENS_MOVEIS')" title="Contas 1231101XX">Bens Móveis<span class="si" id="s_BENS_MOVEIS">⇅</span></th>
  <th style="width:130px" onclick="sort('BENS_MOVEIS_ALMOX')" title="Contas 1231108XX">Almox.<span class="si" id="s_BENS_MOVEIS_ALMOX">⇅</span></th>
  <th style="width:130px" onclick="sort('BENS_MOVEIS_IMPORT')" title="Contas 1231118XX">Import.<span class="si" id="s_BENS_MOVEIS_IMPORT">⇅</span></th>
  <th style="width:140px" onclick="sort('TOTAL_SIGGO')">Total SIGGO<span class="si" id="s_TOTAL_SIGGO">⇅</span></th>
  <th style="width:140px" onclick="sort('SALDO_SISGEPAT')">SISGEPAT<span class="si" id="s_SALDO_SISGEPAT">⇅</span></th>
  <th style="width:140px" onclick="sort('DIFERENCA')">Diferença<span class="si" id="s_DIFERENCA">⇅</span></th>
</tr></thead>
<tbody id="tbody"></tbody>
<tfoot><tr>
  <td class="left" colspan="4" id="ft-label">Total</td>
  <td id="ft-bm"></td><td id="ft-almox"></td><td id="ft-import"></td>
  <td id="ft-siggo"></td><td id="ft-sispat"></td><td id="ft-diff"></td>
</tr></tfoot>
</table>
</div>
</div>

<script>
const _gz = "{dados_b64}";
async function decomp(){{
  const bin = atob(_gz);
  const u8  = Uint8Array.from(bin, c=>c.charCodeAt(0));
  const ds  = new DecompressionStream('gzip');
  const wr  = ds.writable.getWriter(); wr.write(u8); wr.close();
  const buf = await new Response(ds.readable).arrayBuffer();
  return JSON.parse(new TextDecoder().decode(buf));
}}

let DADOS=[], sortCol='COUG', sortDir=1;
const brl = v => 'R$\u00a0'+Number(v).toLocaleString('pt-BR',{{minimumFractionDigits:2,maximumFractionDigits:2}});
const n   = v => parseFloat(v)||0;

function sort(col){{
  if(sortCol===col) sortDir*=-1; else {{sortCol=col;sortDir=1;}}
  document.querySelectorAll('.si').forEach(e=>e.textContent='⇅');
  const el=document.getElementById('s_'+col);
  if(el) el.textContent=sortDir>0?'↑':'↓';
  render();
}}

function filtrar(){{
  const ug  = (document.getElementById('f-ug').value||'').toLowerCase().trim();
  const sub = (document.getElementById('f-sub').value||'').toLowerCase().trim();
  const mode= document.getElementById('f-mode').value;
  return DADOS.filter(r=>{{
    if(ug  && !r.COUG.includes(ug) && !(r.NOUG||'').toLowerCase().includes(ug)) return false;
    if(sub && !r.SUBITEM.includes(sub) && !(r.DESC_SUBITEM||'').toLowerCase().includes(sub)) return false;
    if(mode==='diff'       && Math.abs(r.DIFERENCA)<=0.005) return false;
    if(mode==='only-siggo' && r.SALDO_SISGEPAT!==0) return false;
    if(mode==='only-sispat'&& r.TOTAL_SIGGO!==0) return false;
    return true;
  }});
}}

function render(){{
  const rows = filtrar().sort((a,b)=>{{
    const va=a[sortCol]??'', vb=b[sortCol]??'';
    if(typeof va==='number') return sortDir*(va-vb);
    return sortDir*String(va).localeCompare(String(vb),'pt-BR');
  }});

  const grp = document.getElementById('f-grp').value==='1';
  const tb  = document.getElementById('tbody');
  tb.innerHTML='';

  let tBM=0,tAL=0,tIM=0,tSG=0,tSP=0,tDF=0;
  let ugAtual='', ugBM=0,ugAL=0,ugIM=0,ugSG=0,ugSP=0,ugDF=0;

  function flushUG(){{
    if(!grp||!ugAtual) return;
    const tr=document.createElement('tr'); tr.className='ug-sub';
    const dc=ugDF>0.005?'diff-pos':ugDF<-0.005?'diff-neg':'diff-zero';
    tr.innerHTML=`<td class="left" colspan="4">${{ugAtual}} — subtotal</td>`+
      `<td>${{brl(ugBM)}}</td><td>${{brl(ugAL)}}</td><td>${{brl(ugIM)}}</td>`+
      `<td>${{brl(ugSG)}}</td><td>${{brl(ugSP)}}</td><td class="${{dc}}">${{brl(ugDF)}}</td>`;
    tb.appendChild(tr);
    ugBM=ugAL=ugIM=ugSG=ugSP=ugDF=0;
  }}

  rows.forEach(r=>{{
    if(grp && r.COUG!==ugAtual){{ flushUG(); ugAtual=r.COUG; }}
    const bm=n(r.BENS_MOVEIS),al=n(r.BENS_MOVEIS_ALMOX),im=n(r.BENS_MOVEIS_IMPORT);
    const sg=n(r.TOTAL_SIGGO),sp=n(r.SALDO_SISGEPAT),df=n(r.DIFERENCA);
    tBM+=bm;tAL+=al;tIM+=im;tSG+=sg;tSP+=sp;tDF+=df;
    ugBM+=bm;ugAL+=al;ugIM+=im;ugSG+=sg;ugSP+=sp;ugDF+=df;
    const dc=df>0.005?'diff-pos':df<-0.005?'diff-neg':'diff-zero';
    const tr=document.createElement('tr');
    tr.innerHTML=`<td class="left">${{r.COUG}}</td>`+
      `<td class="left" title="${{r.NOUG}}">${{(r.NOUG||'').substring(0,30)}}</td>`+
      `<td class="left">${{r.SUBITEM}}</td>`+
      `<td class="left" title="${{r.DESC_SUBITEM}}">${{(r.DESC_SUBITEM||'').substring(0,22)}}</td>`+
      `<td>${{brl(bm)}}</td><td>${{brl(al)}}</td><td>${{brl(im)}}</td>`+
      `<td>${{brl(sg)}}</td><td>${{brl(sp)}}</td><td class="${{dc}}">${{brl(df)}}</td>`;
    tb.appendChild(tr);
  }});
  if(grp) flushUG();

  // rodapé
  document.getElementById('ft-bm').textContent    = brl(tBM);
  document.getElementById('ft-almox').textContent  = brl(tAL);
  document.getElementById('ft-import').textContent = brl(tIM);
  document.getElementById('ft-siggo').textContent  = brl(tSG);
  document.getElementById('ft-sispat').textContent = brl(tSP);
  const ftd=document.getElementById('ft-diff');
  ftd.textContent=brl(tDF);
  ftd.className=tDF>0.005?'diff-pos':tDF<-0.005?'diff-neg':'diff-zero';
  document.getElementById('info-linhas').textContent=
    rows.length+' linha(s) exibida(s) de {n_linhas} total.';

  // KPIs dinâmicos (refletem filtro atual)
  document.getElementById('kv-siggo').textContent  = brl(tSG);
  document.getElementById('kv-sispat').textContent = brl(tSP);
  document.getElementById('kv-diff').textContent   = brl(tDF);
  const ugsDiff = new Set(rows.filter(r=>Math.abs(n(r.DIFERENCA))>0.005).map(r=>r.COUG));
  document.getElementById('kv-ugs').textContent    = ugsDiff.size;
  document.getElementById('kv-linhas').textContent = rows.filter(r=>Math.abs(n(r.DIFERENCA))>0.005).length;
}}

function limparFiltros(){{
  ['f-ug','f-sub'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('f-mode').value='all';
  document.getElementById('f-grp').value='1';
  render();
}}

decomp().then(d=>{{ DADOS=d; render(); }});
</script>
</body>
</html>"""
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
                    f"auto: atualiza conciliacao_bens_moveis_sisgepat.html"], check=True)
    subprocess.run(["git", "-C", str(PASTA), "push", "origin", GITHUB_BRANCH], check=True)
    print(f"  Publicado: https://{GITHUB_USER}.github.io/{GITHUB_REPO}/{ARQUIVO_HTML}")


# ── Principal ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mes",     type=int, default=datetime.now().month)
    ap.add_argument("--ano",     type=int, default=datetime.now().year)
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()

    print(f"[{datetime.now():%H:%M:%S}] Conciliação Bens Móveis SIGGO × SISGEPAT ({MESES[a.mes]}/{a.ano})...")
    df, mes, ano, data_corte = extrair(a.mes, a.ano)
    print(f"[{datetime.now():%H:%M:%S}] Gerando HTML...")
    html = gerar_html(df, mes, ano, data_corte)
    publicar(html, a.no_push)
    print(f"[{datetime.now():%H:%M:%S}] Concluído.")

if __name__ == "__main__":
    main()
