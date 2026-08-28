"""
Correspondência UG Ativo × Passivo — Painel Unificado de Validação EQ1–EQ15
Consolida as equações do painel de saldo (EQ1–EQ6) com as equações derivadas
dos Roteiros de Evento (EQ7–EQ15), identificadas via cruzamento EVENTO×EVENTOROTEIRO.

Equações monitoradas:
  EQ1 : 21142XXXX = 113620101 + 113620103          (RPPS patronal)
  EQ2 : 218820101 = 113620102 + 113620104           (Seguridade Social)
  EQ3 : 218820104 = 112120101                       (IR retido na fonte)
  EQ4 : 218820107 = 112120104                       (ICMS retido)
  EQ5 : 214320100+214325100+218820108+218827005 = 112120107  (ISS)
  EQ6 : 218924019 = 112322200                       (Convênios — subgrupo 92)
  EQ7 : 213120101+213125101 = 112220100+112120201+112120202+113821300+113824500
  EQ8 : 218920500 = 112322100                       (Convênios a pagar Intra-OFSS)
  EQ9 : 218920400 = 113820700                       (Infrações/multas intra)
  EQ10: 214320200 = 112120105                       (IPTU)
  EQ11: 214220600 = 112120201                       (Taxa de licença)
  EQ12: 214229900 = 112120103                       (Outros tributos e contribuições)
  EQ13: 218921300 = 113129907                       (Indenizações — série específica)
  EQ14: 218920102+218820199+218820403+218820430+213120199
        +218924004+218924016+218924018 = 113829900+113821200
        (Setorial Financeira 130101 — última; divergência estrutural esperada)

Nota: 218924019 consta somente em EQ6 — removida de EQ14 para evitar dupla contagem.

Uso:
    python extrair_correspondencia_ativo_passivo_eventos.py [--no-push]
"""
import argparse, base64, gzip, json, os, re, subprocess, sys
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
ARQUIVO_HTML = "correspondencia_ativo_passivo_eventos.html"
INSTANT_CLIENT_DIR = r"C:\oracle\instantclient_23_0"

MESES = {0:"Saldo Inicial",1:"Janeiro",2:"Fevereiro",3:"Março",4:"Abril",5:"Maio",6:"Junho",
         7:"Julho",8:"Agosto",9:"Setembro",10:"Outubro",11:"Novembro",12:"Dezembro"}

EQUACOES = [
    # ── EQ1–EQ6: herdadas do painel de saldo ─────────────────────────────────
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
    {"id": "EQ5", "desc": "214320100+214325100+218820108+218827005 = 112120107",
     "passivo_exact": [214320100, 214325100, 218820108, 218827005], "passivo_prefix": None,
     "ativo": [112120107]},
    {"id": "EQ6", "desc": "218924019 = 112322200",
     "passivo_exact": [218924019], "passivo_prefix": None,
     "ativo": [112322200]},

    # ── EQ7–EQ14: derivadas dos roteiros de evento ────────────────────────────
    # EQ7: 213125101 adicionado ao passivo após diagnóstico de divergência
    {"id": "EQ7",  "desc": "213120101+213125101 = 112220100+112120201+112120202+113821300+113824500",
     "passivo_exact": [213120101, 213125101], "passivo_prefix": None,
     "ativo": [112220100, 112120201, 112120202, 113821300, 113824500]},

    {"id": "EQ8",  "desc": "218920500 = 112322100",
     "passivo_exact": [218920500], "passivo_prefix": None,
     "ativo": [112322100]},

    {"id": "EQ9",  "desc": "218920400 = 113820700",
     "passivo_exact": [218920400], "passivo_prefix": None,
     "ativo": [113820700]},

    {"id": "EQ10", "desc": "214320200 = 112120105",
     "passivo_exact": [214320200], "passivo_prefix": None,
     "ativo": [112120105]},

    {"id": "EQ11", "desc": "214220600 = 112120201",
     "passivo_exact": [214220600], "passivo_prefix": None,
     "ativo": [112120201]},

    {"id": "EQ12", "desc": "214229900 = 112120103",
     "passivo_exact": [214229900], "passivo_prefix": None,
     "ativo": [112120103]},

    {"id": "EQ13", "desc": "218921300 = 113129907",
     "passivo_exact": [218921300], "passivo_prefix": None,
     "ativo": [113129907]},

    # EQ14: Setorial Financeira UG 130101 — passivos intra + série 218924XXX.
    # 218924019 foi mantida em EQ6 apenas (evita dupla contagem no total geral).
    # Divergência esperada no par (130101×130101): limitação estrutural de registro
    # da Setorial — o passivo correspondente está parcialmente em EQ3 e EQ6.
    {"id": "EQ14",
     "desc": "218920102+218820199+218820403+218820430+213120199+218924004+218924016+218924018 = 113829900+113821200",
     "passivo_exact": [218920102, 218820199, 218820403, 218820430, 213120199,
                       218924004, 218924016, 218924018],
     "passivo_prefix": None,
     "ativo": [113829900, 113821200]},
]

_CONTAS_ATIVO  = sorted({c for eq in EQUACOES for c in eq["ativo"]})
_CONTAS_EXATAS = sorted({c for eq in EQUACOES for c in eq["passivo_exact"]})
_PREFIXOS      = [eq["passivo_prefix"] for eq in EQUACOES if eq["passivo_prefix"]]

_CONTA_META: dict[str, list[tuple[str, str]]] = {}
for eq in EQUACOES:
    for c in eq["ativo"]:
        _CONTA_META.setdefault(str(c), []).append((eq["id"], "A"))
    for c in eq["passivo_exact"]:
        _CONTA_META.setdefault(str(c), []).append((eq["id"], "P"))


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


SQL = f"""
SELECT
  v.INMES,
  LPAD(TO_CHAR(v.COUG),     6, '0') || '-' ||
  LPAD(TO_CHAR(v.COGESTAO), 5, '0')            AS UG_PROPRIO,
  TO_CHAR(v.COCONTACONTABIL)                    AS COCONTACONTABIL,
  SUBSTR(TRIM(TO_CHAR(v.COCONTACORRENTE)), -12) AS UG_CONTRAPARTE,
  ROUND(SUM(
    CASE SUBSTR(TO_CHAR(v.COCONTACONTABIL), 1, 1)
      WHEN '1' THEN v.VADEBITO  - v.VACREDITO
      WHEN '2' THEN v.VACREDITO - v.VADEBITO
      ELSE 0
    END
  ), 2) AS VLSALDO
FROM {SCHEMA}VSALDOCONTABIL v
WHERE ({_where_contas()})
  AND LENGTH(TRIM(TO_CHAR(v.COCONTACORRENTE))) >= 12
GROUP BY
  v.INMES,
  LPAD(TO_CHAR(v.COUG),     6, '0') || '-' || LPAD(TO_CHAR(v.COGESTAO), 5, '0'),
  TO_CHAR(v.COCONTACONTABIL),
  SUBSTR(TRIM(TO_CHAR(v.COCONTACORRENTE)), -12)
HAVING ROUND(SUM(
    CASE SUBSTR(TO_CHAR(v.COCONTACONTABIL), 1, 1)
      WHEN '1' THEN v.VADEBITO  - v.VACREDITO
      WHEN '2' THEN v.VACREDITO - v.VADEBITO
      ELSE 0
    END
  ), 2) <> 0
ORDER BY v.INMES, UG_PROPRIO, TO_CHAR(v.COCONTACONTABIL)
"""

SQL_CONTA = f"""SELECT TO_CHAR(COCONTACONTABIL) AS COCONTACONTABIL,
                       TRIM(NOCONTACONTABIL)     AS NOCONTACONTABIL
FROM {SCHEMA}VCONTACONTABIL WHERE {_sql_conta()}"""

_UG_RE = re.compile(r"^\d{6}-\d{5}$")


def extrair():
    oracledb.init_oracle_client(lib_dir=INSTANT_CLIENT_DIR)
    with oracledb.connect(user=ORACLE_USER, password=ORACLE_PASS, dsn=ORACLE_DSN) as conn:
        df       = pd.read_sql(SQL,       conn)
        df_conta = pd.read_sql(SQL_CONTA, conn)

    conta_names = dict(zip(df_conta["COCONTACONTABIL"].astype(str),
                           df_conta["NOCONTACONTABIL"]))

    print(f"  Linhas brutas: {len(df):,}")
    df = df[df["UG_CONTRAPARTE"].apply(
        lambda x: bool(_UG_RE.match(str(x))) if pd.notna(x) else False
    )].copy()
    print(f"  Após filtro de padrão UG: {len(df):,}")

    def _eq_lado(conta: str) -> list[tuple[str, str]]:
        if conta in _CONTA_META:
            return _CONTA_META[conta]
        for eq in EQUACOES:
            if eq["passivo_prefix"] and conta.startswith(eq["passivo_prefix"]):
                return [(eq["id"], "P")]
        return []

    # Explode: uma conta pode pertencer a múltiplas equações (ex: 113829900)
    expanded = []
    for _, row in df.iterrows():
        pares = _eq_lado(str(int(row["COCONTACONTABIL"])))
        for eq_id, lado in pares:
            r = row.to_dict()
            r["EQ"]   = eq_id
            r["LADO"] = lado
            expanded.append(r)

    df2 = pd.DataFrame(expanded) if expanded else pd.DataFrame(
        columns=list(df.columns) + ["EQ", "LADO"])

    print(f"  Linhas após explosão por equação: {len(df2):,}")

    records = df2[["INMES","EQ","LADO","UG_PROPRIO","UG_CONTRAPARTE",
                   "COCONTACONTABIL","VLSALDO"]].rename(
        columns={"INMES":"m","EQ":"eq","LADO":"lado","UG_PROPRIO":"ug",
                 "UG_CONTRAPARTE":"cp","COCONTACONTABIL":"ct","VLSALDO":"vl"}
    ).to_dict(orient="records")

    for r in records:
        r["m"]  = int(r["m"])
        r["vl"] = float(r["vl"])

    return records, conta_names


# ── HTML Template ─────────────────────────────────────────────────────────────
HTML_TEMPLATE = r"""<!DOCTYPE html>
<!-- v-corresp-eventos-1 -->
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Correspondência — Ativo × Passivo por Eventos — SIGGO</title>
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
.aviso{background:#fff8e1;border-left:4px solid var(--amber);padding:10px 20px;font-size:12px;color:#5a4000;margin:12px 28px 0;border-radius:6px}
.aviso strong{color:#b7860b}
.fbar{background:var(--surface);border-bottom:1px solid var(--border);padding:14px 28px;display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;margin-top:8px}
.fg{display:flex;flex-direction:column;gap:4px}
.fg label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
.fg select{border:1.5px solid var(--border);border-radius:6px;padding:7px 28px 7px 10px;font-size:12.5px;min-width:150px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath fill='%236b7a99' d='M0 0l5 6 5-6z'/%3E%3C/svg%3E") no-repeat right 9px center;color:var(--text);cursor:pointer;appearance:none}
.fg select:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-wrap{position:relative}
.ac-input{border:1.5px solid var(--border);border-radius:6px;padding:7px 32px 7px 10px;font-size:12.5px;width:100%;background:#fff;color:var(--text);transition:border-color .15s}
.ac-input:focus{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}
.ac-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);background:none;border:none;cursor:pointer;color:var(--muted);font-size:14px;display:none;line-height:1;padding:2px}
.ac-dd{position:absolute;top:calc(100% + 4px);left:0;right:0;min-width:280px;background:#fff;border:1.5px solid var(--teal);border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);z-index:200;max-height:240px;overflow-y:auto;display:none}
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
.btn-warn{background:#fff3cd;color:#856404;border:1px solid #ffe69c}
.btn-warn.ativo{background:var(--amber);color:#fff}
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
table{width:100%;border-collapse:collapse;min-width:800px}
thead th{background:var(--navy);color:#c8d8ec;padding:11px 14px;font-size:11px;font-weight:600;text-align:right;white-space:nowrap;letter-spacing:.3px}
thead th.left{text-align:left}
tr.row-eq{background:#1e3267;cursor:pointer}
tr.row-eq:hover td{filter:brightness(1.1)}
tr.row-eq td{color:#e8f0fc;font-weight:700;padding:10px 14px;font-size:12px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tr.row-eq td.left{text-align:left}
tr.row-pair{background:var(--surface);cursor:default}
tr.row-pair.alt{background:var(--row-alt)}
tr.row-pair:hover td{background:var(--hover)}
tr.row-pair td{padding:8px 14px 8px 36px;font-size:12px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--border);font-variant-numeric:tabular-nums}
tr.row-pair td.left{text-align:left;padding-left:36px}
tr.row-pair td.ug-cell{text-align:left;padding-left:36px}
tr.row-pair td.ug-cell-p{text-align:left}
tfoot tr td{background:#f0f4fb;font-weight:700;border-top:2px solid var(--teal);padding:10px 14px;font-size:12.5px;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
tfoot tr td.left{text-align:left}
.tog{display:inline-block;width:14px;text-align:center;font-size:10px;margin-right:4px}
.ug-code{font-weight:600;color:var(--navy);font-size:12px}
.ug-sub{font-size:10px;color:var(--muted);display:block;margin-top:1px}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center">
    <div class="hlogo">&#9878;</div>
    <h1>Correspond&#234;ncia &#8212; Ativo &#215; Passivo &#8212; Painel Unificado
      <span>SIGGO &#183; Exerc&#237;cio 2026 &#183; EQ1&#8211;EQ14 &#8226; Valida&#231;&#227;o</span>
    </h1>
  </div>
  <span id="ts">__TIMESTAMP__</span>
</header>

<div class="aviso">
  <strong>&#9888; Painel de valida&#231;&#227;o &#8212; EQ1&#8211;EQ14.</strong>
  <strong>EQ14 &#8212; Setorial Financeira (UG 130101):</strong> diverg&#234;ncia estrutural esperada.
  O ativo 113829900 registra o pr&#243;prio c&#243;digo como contraparte; o passivo correspondente
  est&#225; em outras UGs &#8212; parte j&#225; capturada em EQ3 e EQ6, parte na s&#233;rie 218924XXX de EQ14.
  O par (130101&#x2194;130101) aparece no ativo sem contrapartida de passivo na mesma chave de agrupamento &#8212; isso &#233; inerente ao padr&#227;o de registro da Setorial, n&#227;o erro cont&#225;bil.
</div>

<div class="fbar">
  <div class="fg">
    <label>M&#234;s</label>
    <select id="fm" onchange="aplicar()"></select>
  </div>
  <div class="fg" style="min-width:200px">
    <label>Unidade Gestora</label>
    <div class="ac-wrap">
      <input id="fug-input" class="ac-input" style="min-width:190px"
        placeholder="ex: 130101-00001"
        autocomplete="off" oninput="onUGInput()" onfocus="onUGInput()">
      <button class="ac-clear" id="fug-clear" onclick="limUGFil()" title="Limpar">&#x2715;</button>
      <div class="ac-dd" id="fug-dd"></div>
    </div>
  </div>
  <div class="fg" style="min-width:280px">
    <label>Conta Cont&#225;bil</label>
    <div class="ac-wrap">
      <input id="fc-input" class="ac-input" style="min-width:270px"
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
    <button id="btn-div" class="btn btn-warn" onclick="toggleDivOnly()">&#9650;&#9660; Diferen&#231;as</button>
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
      <th class="left" style="min-width:220px">Equa&#231;&#227;o / UG Ativo</th>
      <th class="left" style="min-width:120px">Conta Ativo</th>
      <th class="left" style="min-width:150px">UG Passivo</th>
      <th class="left" style="min-width:120px">Conta Passivo</th>
      <th style="width:165px">Saldo Ativo</th>
      <th style="width:165px">Saldo Passivo</th>
      <th style="width:150px">Diferen&#231;a</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
    <tfoot><tr id="tfoot"></tr></tfoot>
  </table>
</div>

<script>
const EQUACOES     = __EQUACOES__;
const CONTA_NAMES  = __CONTA_NAMES__;
const MESES_PT = {0:'Saldo Inicial',1:'Janeiro',2:'Fevereiro',3:'Mar\u00e7o',4:'Abril',5:'Maio',6:'Junho',
                  7:'Julho',8:'Agosto',9:'Setembro',10:'Outubro',11:'Novembro',12:'Dezembro'};
const EQ_COLORS = ['var(--red)','var(--amber)','#2e7d32','#5e35b1','#e65100','#00695c',
                   '#0277bd','#6d4c41','#37474f','#ad1457','#558b2f','#4527a0','var(--teal)'];

function decomp(b64){
  const s=b64.replace(/-/g,'+').replace(/_/g,'/');
  const bin=atob(s),buf=new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++)buf[i]=bin.charCodeAt(i);
  const ds=new DecompressionStream('gzip');
  const w=ds.writable.getWriter();w.write(buf);w.close();
  return new Response(ds.readable).json();
}
decomp('__DADOS__').then(p=>{
  window.ALL=p.rows.map(r=>Object.fromEntries(p.cols.map((k,i)=>[k,r[i]])));
  init();
});

function n(v){return v==null||v===''?0:+v||0;}
function brl(v){return new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(v);}
function vc(v){return Math.abs(v)<0.005?'vz':v>0?'vp':'vn';}

let ugFil='', contaFil='', divOnly=false, curData=null, ugList=[];

function onUGInput(){
  const v=document.getElementById('fug-input').value.trim();
  ugFil=v;
  document.getElementById('fug-clear').style.display=v?'block':'none';
  const dd=document.getElementById('fug-dd');
  if(!v){dd.style.display='none';aplicar();return;}
  const q=v.toLowerCase();
  const matches=ugList.filter(ug=>ug.toLowerCase().includes(q)).slice(0,25);
  if(!matches.length){
    dd.innerHTML='<div class="ac-dd-empty">Nenhuma UG encontrada</div>';
    dd.style.display='block';
  } else {
    dd.innerHTML=matches.map(ug=>'<div class="ac-dd-item" onmousedown="selUG(\''+ug+'\')">'
      +'<strong>'+ug+'</strong></div>').join('');
    dd.style.display='block';
  }
  aplicar();
}
function selUG(code){
  ugFil=code;
  document.getElementById('fug-input').value=code;
  document.getElementById('fug-clear').style.display='block';
  document.getElementById('fug-dd').style.display='none';
  aplicar();
}
function limUGFil(){
  ugFil='';
  document.getElementById('fug-input').value='';
  document.getElementById('fug-clear').style.display='none';
  document.getElementById('fug-dd').style.display='none';
  aplicar();
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
  if(!e.target.closest('.ac-wrap')){
    document.getElementById('fug-dd').style.display='none';
    document.getElementById('fc-dd').style.display='none';
  }
});

function toggleDivOnly(){
  divOnly=!divOnly;
  const btn=document.getElementById('btn-div');
  btn.classList.toggle('ativo',divOnly);
  aplicar();
}

function buildEqData(maxMes){
  const acc={};
  window.ALL.filter(r=>n(r.m)<=maxMes).forEach(r=>{
    const ugA = r.lado==='A' ? r.ug : r.cp;
    const ugP = r.lado==='P' ? r.ug : r.cp;
    const key  = r.eq+'|'+ugA+'|'+ugP;
    if(!acc[key]) acc[key]={eq:r.eq,ug_a:ugA,ug_p:ugP,sa:0,sp:0,ca:new Set(),cp_:new Set()};
    const e=acc[key];
    if(r.lado==='A'){e.sa=Math.round((e.sa+n(r.vl))*100)/100;e.ca.add(r.ct);}
    else            {e.sp=Math.round((e.sp+n(r.vl))*100)/100;e.cp_.add(r.ct);}
  });

  const byEq={};
  Object.values(acc).forEach(e=>{
    if(Math.abs(e.sa)<0.005&&Math.abs(e.sp)<0.005)return;
    if(!byEq[e.eq])byEq[e.eq]={pairs:[],totA:0,totP:0};
    const diff=Math.round((e.sa-e.sp)*100)/100;
    byEq[e.eq].pairs.push({
      ug_a:e.ug_a,ug_p:e.ug_p,sa:e.sa,sp:e.sp,diff,
      ca:[...e.ca].sort(),cp:[...e.cp_].sort()
    });
    byEq[e.eq].totA=Math.round((byEq[e.eq].totA+e.sa)*100)/100;
    byEq[e.eq].totP=Math.round((byEq[e.eq].totP+e.sp)*100)/100;
  });

  return EQUACOES.map(eq=>({
    eq,
    pairs:(byEq[eq.id]?.pairs||[]).sort((a,b)=>a.ug_a.localeCompare(b.ug_a)||a.ug_p.localeCompare(b.ug_p)),
    totA:byEq[eq.id]?.totA||0,
    totP:byEq[eq.id]?.totP||0,
    div:Math.round(((byEq[eq.id]?.totA||0)-(byEq[eq.id]?.totP||0))*100)/100
  }));
}

function mesLabel(m){return MESES_PT[Number(m)]||('M'+m);}

function pairMatchFil(p){
  if(ugFil){
    const q=ugFil.toLowerCase();
    if(!p.ug_a.toLowerCase().includes(q)&&!p.ug_p.toLowerCase().includes(q))return false;
  }
  if(contaFil){
    const q=contaFil.split('\u2014')[0].trim().toLowerCase();
    if(![...p.ca,...p.cp].some(c=>c.toLowerCase().includes(q)))return false;
  }
  if(divOnly&&Math.abs(p.diff)<0.005)return false;
  return true;
}

function aplicar(){
  const maxMes=Number(document.getElementById('fm').value);
  const showFil=document.getElementById('fs').value;
  const period=mesLabel(maxMes);
  curData=buildEqData(maxMes);

  let html='',totA=0,totP=0,totD=0,nEQ=0,nPairs=0;
  const tb=document.getElementById('tbody');
  const tf=document.getElementById('tfoot');

  curData.forEach((d,i)=>{
    const filtPairs=d.pairs.filter(pairMatchFil);
    if(!filtPairs.length)return;
    const eqTotA=filtPairs.reduce((s,p)=>s+p.sa,0);
    const eqTotP=filtPairs.reduce((s,p)=>s+p.sp,0);
    const eqDiv =Math.round((eqTotA-eqTotP)*100)/100;
    if(showFil==='com_div'&&Math.abs(eqDiv)<0.005)return;
    if(showFil==='sem_div'&&Math.abs(eqDiv)>=0.005)return;

    const eid='eq'+i;
    const col=EQ_COLORS[i%EQ_COLORS.length];
    const dCls=Math.abs(eqDiv)<0.005?'vz':eqDiv>0?'vp':'vn';

    html+='<tr class="row-eq" onclick="toggle(\''+eid+'\')">'
      +'<td class="left" colspan="2" style="border-left:3px solid '+col+'">'
      +'<span class="tog" id="tog_'+eid+'">&#9658;</span>'
      +'<strong style="color:'+col+'">'+d.eq.id+'</strong>'
      +' <span style="font-weight:400;font-size:11px;opacity:.8">'+d.eq.desc+'</span></td>'
      +'<td class="left" colspan="2" style="font-size:11.5px;font-weight:400;opacity:.8">'+period+'</td>'
      +'<td class="'+vc(eqTotA)+'">'+brl(eqTotA)+'</td>'
      +'<td class="'+vc(eqTotP)+'">'+brl(eqTotP)+'</td>'
      +'<td class="'+dCls+'" style="font-weight:800">'+brl(eqDiv)+'</td>'
      +'</tr>';

    filtPairs.forEach((p,j)=>{
      const dv=p.diff;
      const dc=Math.abs(dv)<0.005?'vz':dv>0?'vp':'vn';
      const caStr=p.ca.length?p.ca.join(', '):'&#8212;';
      const cpStr=p.cp.length?p.cp.join(', '):'&#8212;';
      html+='<tr class="row-pair'+(j%2?' alt':'')+'" data-par="'+eid+'" style="display:none">'
        +'<td class="ug-cell"><span class="ug-code">'+p.ug_a+'</span></td>'
        +'<td class="left" style="color:var(--muted);font-size:11.5px">'+caStr+'</td>'
        +'<td class="ug-cell-p"><span class="ug-code">'+p.ug_p+'</span></td>'
        +'<td class="left" style="color:var(--muted);font-size:11.5px">'+cpStr+'</td>'
        +'<td class="'+(Math.abs(p.sa)>=0.005?vc(p.sa):'vz')+'">'+(p.ca.length?(Math.abs(p.sa)>=0.005?brl(p.sa):'<span title="Saldo líquido zero">R$ 0,00</span>'):'&#8212;')+'</td>'
        +'<td class="'+(Math.abs(p.sp)>=0.005?vc(p.sp):'vz')+'">'+(p.cp.length?(Math.abs(p.sp)>=0.005?brl(p.sp):'<span title="Saldo líquido zero">R$ 0,00</span>'):'&#8212;')+'</td>'
        +'<td class="'+dc+'" style="font-weight:700">'+(Math.abs(dv)>=0.005?brl(dv):'&#8212;')+'</td>'
        +'</tr>';
    });

    totA=Math.round((totA+eqTotA)*100)/100;
    totP=Math.round((totP+eqTotP)*100)/100;
    totD=Math.round((totD+eqDiv)*100)/100;
    nEQ++; nPairs+=filtPairs.length;
  });

  tb.innerHTML=html||'<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--muted)">Nenhum registro encontrado.</td></tr>';
  tf.innerHTML='<td class="left" colspan="4">Total Geral &middot; '+nEQ+' equa\u00e7\u00e3o(es) &middot; '+nPairs+' par(es) de UG &middot; '+period+'</td>'
    +'<td class="'+vc(totA)+'">'+brl(totA)+'</td>'
    +'<td class="'+vc(totP)+'">'+brl(totP)+'</td>'
    +'<td class="'+vc(totD)+'" style="font-weight:800">'+brl(totD)+'</td>';
  document.getElementById('cnt').textContent=
    nEQ+' equa\u00e7\u00e3o(es) \u00b7 '+nPairs+' par(es) de UG \u00b7 '+period;
  kpis(totA,totP,totD);
}

function kpis(totA,totP,totD){
  if(!curData)return;
  const nDiv=curData.filter(d=>Math.abs(d.div)>=0.005).length;
  const dcT=Math.abs(totD)<0.005?'ko':totD>0?'kw':'ka';
  document.getElementById('krow-total').innerHTML=
    '<div class="kpi-total-row">'
    +'<div class="kpi"><div class="kl">Ativo</div><div class="kv '+vc(totA)+'">'+brl(totA)+'</div></div>'
    +'<div class="kpi"><div class="kl">Passivo</div><div class="kv '+vc(totP)+'">'+brl(totP)+'</div></div>'
    +'<div class="kpi '+dcT+'"><div class="kl">Diferen\u00e7a (A\u2212P)</div>'
    +'<div class="kv '+vc(totD)+'">'+brl(totD)+'</div>'
    +'<div class="ks"><span class="badge '+(nDiv>0?'br':'bg')+'">'+nDiv+' eq. c/ diverg\u00eancia</span></div></div>'
    +'</div>';
  let khtml='<div class="kpi-row">';
  curData.forEach((d,i)=>{
    if(!d.pairs.length)return;
    const col=EQ_COLORS[i%EQ_COLORS.length];
    const dc=Math.abs(d.div)<0.005?'ko':d.div>0?'kw':'ka';
    const nD=d.pairs.filter(p=>Math.abs(p.diff)>=0.005).length;
    khtml+='<div class="kpi-group">'
      +'<div class="kpi-group-title" style="border-left:3px solid '+col+'">'
      +d.eq.id+'<span style="font-weight:400;margin-left:8px;opacity:.65;font-size:8.5px;text-transform:none">'+d.eq.desc+'</span></div>'
      +'<div class="kpi-group-inner">'
      +'<div class="kpi"><div class="kl">Ativo</div><div class="kv '+vc(d.totA)+'">'+brl(d.totA)+'</div>'
      +'<div class="ks">'+d.pairs.length+' par(es)</div></div>'
      +'<div class="kpi"><div class="kl">Passivo</div><div class="kv '+vc(d.totP)+'">'+brl(d.totP)+'</div></div>'
      +'<div class="kpi '+dc+'"><div class="kl">Diferen\u00e7a</div><div class="kv '+vc(d.div)+'">'+brl(d.div)+'</div>'
      +(nD>0?'<div class="ks"><span class="badge br">'+nD+' c/ div.</span></div>':'')+'</div>'
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
  const aberto=tog&&tog.innerHTML.includes('9660');
  if(aberto){fecharDesc(id);if(tog)tog.innerHTML='&#9658;';}
  else{document.querySelectorAll('[data-par="'+id+'"]').forEach(tr=>tr.style.display='');if(tog)tog.innerHTML='&#9660;';}
}
function fecharDesc(id){
  document.querySelectorAll('[data-par="'+id+'"]').forEach(tr=>{tr.style.display='none';});
}
function expandirTudo(){
  document.querySelectorAll('[data-par]').forEach(tr=>tr.style.display='');
  document.querySelectorAll('.tog').forEach(el=>el.innerHTML='&#9660;');
}
function recolherTudo(){
  document.querySelectorAll('[data-par]').forEach(tr=>tr.style.display='none');
  document.querySelectorAll('.tog').forEach(el=>el.innerHTML='&#9658;');
}

function limpar(){
  const meses=[...new Set(window.ALL.map(r=>n(r.m)))].sort((a,b)=>a-b);
  document.getElementById('fm').value=meses[meses.length-1]||'';
  document.getElementById('fs').value='todos';
  divOnly=false;
  document.getElementById('btn-div').classList.remove('ativo');
  limUGFil(); limContaFil();
}

function exportar(){
  if(!curData)return;
  const period=mesLabel(document.getElementById('fm').value);
  const hdr=['Equacao','Descricao','UG Ativo','Contas Ativo','UG Passivo','Contas Passivo',
             'Saldo Ativo','Saldo Passivo','Diferenca','Periodo'];
  const rows=[];
  curData.forEach(d=>{
    d.pairs.forEach(p=>{
      rows.push([d.eq.id,d.eq.desc,p.ug_a,p.ca.join(', '),p.ug_p,p.cp.join(', '),
                 p.sa.toFixed(2),p.sp.toFixed(2),p.diff.toFixed(2),period]);
    });
  });
  const csv=[hdr,...rows].map(r=>r.map(v=>'"'+String(v).replace(/"/g,'""')+'"').join(';')).join('\r\n');
  const a=document.createElement('a');
  a.href='data:text/csv;charset=utf-8,\uFEFF'+encodeURIComponent(csv);
  a.download='correspondencia_eventos_ug.csv';a.click();
}

function init(){
  const meses=[...new Set(window.ALL.map(r=>n(r.m)))].sort((a,b)=>a-b);
  const fm=document.getElementById('fm');
  fm.innerHTML='';
  meses.forEach(m=>{
    const o=document.createElement('option');
    o.value=m;o.textContent=MESES_PT[m]||('M'+m);fm.appendChild(o);
  });
  if(meses.length)fm.value=meses[meses.length-1];
  const ugSet=new Set();
  window.ALL.forEach(r=>{ugSet.add(r.ug);ugSet.add(r.cp);});
  ugList=[...ugSet].filter(u=>u&&/^\d{6}-\d{5}$/.test(u)).sort();
  aplicar();
}
</script>
</body>
</html>
"""


def gerar_html(records: list, conta_names: dict) -> str:
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")

    cols = ["m","eq","lado","ug","cp","ct","vl"]
    rows = [[r[c] for c in cols] for r in records]
    payload = {"cols": cols, "rows": rows}
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    compressed = gzip.compress(raw.encode("utf-8"), compresslevel=9)
    b64 = base64.urlsafe_b64encode(compressed).decode()

    eq_js = json.dumps([{"id": e["id"], "desc": e["desc"]} for e in EQUACOES],
                       ensure_ascii=False)
    cn_js = json.dumps(conta_names, ensure_ascii=False)

    html = HTML_TEMPLATE
    html = html.replace("'__DADOS__'", f"'{b64}'")
    html = html.replace("__TIMESTAMP__", ts)
    html = html.replace("__EQUACOES__",  eq_js)
    html = html.replace("__CONTA_NAMES__", cn_js)
    return html


def publicar(html: str, no_push: bool):
    dest = PASTA / ARQUIVO_HTML
    dest.write_text(html, encoding="utf-8")
    print(f"  HTML gravado: {dest} ({len(html)//1024} KB)")

    if os.environ.get("NO_GIT_PUSH") == "1" or no_push:
        print("  [NO_GIT_PUSH] Push ignorado.")
        return

    subprocess.run(["git", "-C", str(PASTA), "add", ARQUIVO_HTML], check=True)
    subprocess.run(["git", "-C", str(PASTA), "commit", "-m",
                    f"auto: atualiza {ARQUIVO_HTML} -- {datetime.now():%d/%m/%Y %H:%M}"],
                   check=True)
    subprocess.run(["git", "-C", str(PASTA), "push", "origin", "main"], check=True)
    print("  Publicado no GitHub Pages.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    print("Extraindo dados do Oracle (EQ1–EQ14)…")
    records, conta_names = extrair()
    print(f"  Registros válidos: {len(records):,}")

    print("Gerando HTML…")
    html = gerar_html(records, conta_names)
    print(f"  Tamanho: {len(html)//1024} KB")

    publicar(html, args.no_push)
    print("Concluído.")


if __name__ == "__main__":
    main()
