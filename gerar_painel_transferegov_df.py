"""
Gera o painel HTML consolidado (propostas/convenios, situacao e valores)
a partir dos CSVs extraidos por extrair_transferegov_df.py.

Uma aba por modulo do TransfereGov:
  1. Gestao de Parcerias  (proposta + parceria)
  2. Transferencias Especiais (plano de acao + beneficiario)
  3. Fundo a Fundo (plano de acao)
  4. SICONV legado (convenio + proposta)

Salva transferegov_df_painel.html na mesma pasta.
"""

import json
from pathlib import Path

import pandas as pd

PASTA = Path(__file__).resolve().parent


def carregar(nome, **kwargs):
    return pd.read_csv(PASTA / nome, low_memory=False, **kwargs)


def num(serie):
    return pd.to_numeric(serie, errors="coerce").fillna(0)


def normalizar_cnpj(serie):
    """Deixa so os digitos e preenche com zeros a esquerda ate 14 posicoes."""
    return serie.astype(str).str.replace(r"\D", "", regex=True).str.zfill(14)


def carregar_cnpjs_gdf() -> set:
    """
    Le a lista de referencia (unidadegestora_bruto.csv, extraida da tabela
    MIL2026.UNIDADEGESTORA - campo NUCGC) e devolve o conjunto de CNPJs
    (14 digitos, com zeros a esquerda) que pertencem ao GDF (orgaos/entidades
    da administracao direta e indireta), mais o CNPJ do proprio ente
    federativo Distrito Federal (conta unica: 00394601000126).
    """
    caminho = PASTA / "cnpjs_gdf.csv"
    if not caminho.exists():
        return set()
    df = pd.read_csv(caminho, dtype=str)
    return set(normalizar_cnpj(df["NUCGC"]))


CNPJS_GDF = carregar_cnpjs_gdf()


def marcar_vinculo_gdf(df: pd.DataFrame, coluna_cnpj: str) -> pd.Series:
    """
    Classifica cada linha como 'GDF' (CNPJ bate com a lista de UGs do SIGGO
    ou e o CNPJ do ente federativo DF) ou 'Privado/Outro' (associacoes,
    empresas, cooperativas etc. apenas sediadas no DF, sem relacao com o
    governo distrital).
    """
    if coluna_cnpj not in df.columns:
        return pd.Series(["(sem CNPJ)"] * len(df), index=df.index)
    cnpj_norm = normalizar_cnpj(df[coluna_cnpj])
    return cnpj_norm.map(lambda c: "GDF" if c in CNPJS_GDF and c != "00000000000000" else "Privado/Outro")


# ---------------------------------------------------------------------------
# 1) Gestao de Parcerias
# ---------------------------------------------------------------------------

def montar_parcerias():
    proposta = carregar("df_parcerias_proposta.csv")
    parceria = carregar("df_parcerias_parceria.csv")

    df = proposta.merge(
        parceria[["id_proposta", "cd_parceria", "in_situacao_parceria", "dh_assinatura"]],
        on="id_proposta", how="left",
    )

    df["situacao"] = df["in_situacao_parceria"].fillna(df["situacao_proposta"])
    df["valor"] = num(df.get("nr_vlr_total")).where(
        num(df.get("nr_vlr_total")) > 0, num(df.get("vl_total_planejamento_gastos"))
    )

    saida = pd.DataFrame({
        "Identificador": df["cd_parceria"].fillna(df["id_proposta"].astype("Int64").astype(str)),
        "Ente recebedor": df["nm_ente_recebedor"],
        "Vínculo GDF": marcar_vinculo_gdf(df, "cnpj_ente_recebedor"),
        "Natureza jurídica": df["nm_natureza_juridica"],
        "Município": df["nm_municipio_recebedor"],
        "Órgão superior": df["nm_unidade_gestora"],
        "Objeto": df["ds_objeto"].astype(str).str.slice(0, 140),
        "Situação": df["situacao"],
        "Ano": df["ano_proposta"],
        "Valor (R$)": df["valor"],
        "Assinatura": df["dh_assinatura"],
    })
    return saida


# ---------------------------------------------------------------------------
# 2) Transferencias Especiais
# ---------------------------------------------------------------------------

def montar_especiais():
    plano = carregar("df_especiais_plano_acao.csv")
    beneficiario = carregar("df_especiais_beneficiario.csv")
    if plano.empty:
        return pd.DataFrame(columns=["Identificador", "Beneficiário", "Vínculo GDF", "Parlamentar", "Objeto", "Situação", "Ano", "Valor (R$)"])

    # Neste modulo (\"emendas Pix\"), o recurso vai direto para a conta unica
    # do ente federativo - por isso o beneficiario e sempre o proprio DF,
    # e nao ha \"ente recebedor\" individualizado por plano de acao.
    plano = plano.merge(beneficiario[["id_beneficiario", "nome_beneficiario", "cnpj_beneficiario"]], on="id_beneficiario", how="left")
    plano["valor"] = num(plano.get("valor_custeio_plano_acao")) + num(plano.get("valor_investimento_plano_acao"))

    saida = pd.DataFrame({
        "Identificador": plano["codigo_plano_acao"],
        "Beneficiário": plano["nome_beneficiario"],
        "Vínculo GDF": marcar_vinculo_gdf(plano, "cnpj_beneficiario"),
        "Parlamentar autor da emenda": plano["nome_parlamentar_emenda_plano_acao"],
        "Objeto": plano["nome_objeto"].fillna("(não detalhado no plano de ação)"),
        "Situação": plano["situacao_plano_acao"],
        "Ano": plano["ano_plano_acao"],
        "Valor (R$)": plano["valor"],
    })
    return saida


# ---------------------------------------------------------------------------
# 3) Fundo a Fundo
# ---------------------------------------------------------------------------

def montar_fundoafundo():
    plano = carregar("df_fundoafundo_plano_acao.csv")
    if plano.empty:
        return pd.DataFrame(columns=["Identificador", "Ente recebedor", "Fundo vinculado", "Situação", "Vigência", "Valor (R$)"])

    saida = pd.DataFrame({
        "Identificador": plano["codigo_plano_acao"],
        "Ente recebedor": plano["nome_ente_recebedor_plano_acao"],
        "Vínculo GDF": marcar_vinculo_gdf(plano, "cnpj_ente_recebedor_plano_acao"),
        "Fundo vinculado": plano["nome_fundo_vinculado_plano_acao"],
        "Órgão repassador": plano["nome_orgao_repassador_plano_acao"],
        "Situação": plano["situacao_plano_acao"],
        "Início vigência": plano["data_inicio_vigencia_plano_acao"],
        "Valor (R$)": num(plano.get("valor_total_plano_acao")),
    })
    return saida


# ---------------------------------------------------------------------------
# 4) SICONV legado
# ---------------------------------------------------------------------------

def montar_siconv():
    convenio = carregar("df_siconv_convenio.csv", dtype={"NR_CONVENIO": str})
    proposta = carregar("df_siconv_proposta.csv")

    df = convenio.merge(
        proposta[["ID_PROPOSTA", "DESC_ORGAO_SUP", "NM_PROPONENTE", "MUNIC_PROPONENTE", "IDENTIF_PROPONENTE"]],
        on="ID_PROPOSTA", how="left",
    )

    saida = pd.DataFrame({
        "Nº Convênio": df["NR_CONVENIO"],
        "Proponente": df["NM_PROPONENTE"],
        "Vínculo GDF": marcar_vinculo_gdf(df, "IDENTIF_PROPONENTE"),
        "Município": df["MUNIC_PROPONENTE"],
        "Órgão superior": df["DESC_ORGAO_SUP"],
        "Situação": df["SIT_CONVENIO"],
        "Ano": df["ANO"],
        "Valor global (R$)": num(df.get("VL_GLOBAL_CONV")),
        "Valor repasse (R$)": num(df.get("VL_REPASSE_CONV")),
        "Valor empenhado (R$)": num(df.get("VL_EMPENHADO_CONV")),
        "Valor desembolsado (R$)": num(df.get("VL_DESEMBOLSADO_CONV")),
        "UG emitente": df["UG_EMITENTE"],
    })
    return saida


# ---------------------------------------------------------------------------
# Montagem do HTML
# ---------------------------------------------------------------------------

ABAS = [
    {
        "id": "parcerias",
        "titulo": "Gestão de Parcerias",
        "fonte": "API /parcerias — endpoints /proposta + /parceria",
        "df": None,
    },
    {
        "id": "especiais",
        "titulo": "Transferências Especiais",
        "fonte": "API /especiais — endpoints /beneficiarios-especiais + /planos-acao-especiais",
        "df": None,
    },
    {
        "id": "fundoafundo",
        "titulo": "Fundo a Fundo",
        "fonte": "API /fundoafundo — endpoint /planos-acao",
        "df": None,
    },
    {
        "id": "siconv",
        "titulo": "SICONV Legado",
        "fonte": "Download CSV — siconv_convenio.csv + siconv_proposta.csv",
        "df": None,
    },
]


def fmt_valor(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    return f"{v:,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")


def montar_tabela_html(df: pd.DataFrame, aba_id: str) -> str:
    colunas = list(df.columns)
    registros = df.fillna("").to_dict(orient="records")
    for r in registros:
        for c in colunas:
            if "Valor" in c or "valor" in c:
                r[c] = fmt_valor(r[c]) if r[c] != "" else ""

    thead = "".join(f'<th onclick="ordenar(\'{aba_id}\',{i})">{c}</th>' for i, c in enumerate(colunas))
    dados_json = json.dumps(registros, ensure_ascii=False)

    chips_vinculo = ""
    if "Vínculo GDF" in colunas:
        chips_vinculo = f"""
        <div class="chips" id="chips-{aba_id}">
          <button class="chip ativo" data-valor="" onclick="filtrarVinculo('{aba_id}','', this)">Todos</button>
          <button class="chip" data-valor="GDF" onclick="filtrarVinculo('{aba_id}','GDF', this)">Só GDF</button>
          <button class="chip" data-valor="Privado/Outro" onclick="filtrarVinculo('{aba_id}','Privado/Outro', this)">Só privado/outro</button>
        </div>
        """

    return f"""
    {chips_vinculo}
    <div class="painel-busca">
      <input type="text" id="busca-{aba_id}" placeholder="Buscar em {len(df)} registros..." oninput="filtrar('{aba_id}')">
      <span class="contador" id="contador-{aba_id}">{len(df)} registros</span>
    </div>
    <div class="tabela-wrap">
      <table id="tabela-{aba_id}">
        <thead><tr>{thead}</tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <script>DADOS['{aba_id}'] = {dados_json}; COLUNAS['{aba_id}'] = {json.dumps(colunas, ensure_ascii=False)};</script>
    """


def montar_resumo(df: pd.DataFrame, coluna_situacao: str, coluna_valor: str) -> str:
    if df.empty or coluna_situacao not in df.columns:
        return '<div class="resumo-vazio">Sem registros para este módulo.</div>'

    vinculo_html = ""
    if "Vínculo GDF" in df.columns:
        c_gdf = int((df["Vínculo GDF"] == "GDF").sum())
        c_priv = int((df["Vínculo GDF"] == "Privado/Outro").sum())
        vinculo_html = f"""
        <div class="resumo resumo-vinculo">
          <div class="card card-gdf"><div class="card-num">{c_gdf}</div><div class="card-label">Órgãos/entidades do GDF</div></div>
          <div class="card card-priv"><div class="card-num">{c_priv}</div><div class="card-label">Privado/outro (sediado no DF)</div></div>
        </div>
        """

    contagem = df[coluna_situacao].fillna("(não informado)").value_counts()

    cards = "".join(
        f'<div class="card"><div class="card-num">{qtd}</div><div class="card-label">{sit}</div></div>'
        for sit, qtd in contagem.items()
    )
    return f"""
    {vinculo_html}
    <div class="resumo">
      <div class="card card-total">
        <div class="card-num">{len(df)}</div>
        <div class="card-label">Total de registros</div>
      </div>
      {cards}
    </div>
    """


def main():
    dados = {
        "parcerias": montar_parcerias(),
        "especiais": montar_especiais(),
        "fundoafundo": montar_fundoafundo(),
        "siconv": montar_siconv(),
    }

    abas_html = []
    conteudo_html = []
    for i, aba in enumerate(ABAS):
        aid = aba["id"]
        df = dados[aid]
        ativo = "ativo" if i == 0 else ""
        abas_html.append(f'<button class="aba-btn {ativo}" data-aba="{aid}" onclick="mostrarAba(\'{aid}\')">{aba["titulo"]} <span class="badge">{len(df)}</span></button>')

        resumo_html = montar_resumo(df, "Situação", [c for c in df.columns if "Valor" in c][0] if any("Valor" in c for c in df.columns) else "")
        tabela_html = montar_tabela_html(df, aid)

        conteudo_html.append(f"""
        <section class="aba-conteudo {'ativo' if i == 0 else ''}" id="conteudo-{aid}">
          <p class="fonte-info">Fonte: {aba['fonte']}</p>
          {resumo_html}
          {tabela_html}
        </section>
        """)

    html = TEMPLATE.format(
        abas_nav="".join(abas_html),
        abas_conteudo="".join(conteudo_html),
        data_geracao=pd.Timestamp.now().strftime("%d/%m/%Y %H:%M"),
    )

    saida = PASTA / "transferegov_df_painel.html"
    saida.write_text(html, encoding="utf-8")
    print(f"Painel salvo em {saida}")


TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TransfereGov x DF — Visão Consolidada</title>
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
header h1{{font-size:15px;font-weight:700;letter-spacing:.3px}}
header h1 span{{font-weight:400;color:#9ab0cc;font-size:12px;display:block;text-transform:none;letter-spacing:0;margin-top:1px}}
#ts{{font-size:11px;color:#7a99bb;white-space:nowrap}}

.abas-nav{{background:var(--surface);border-bottom:1px solid var(--border);padding:0 28px;display:flex;gap:4px;overflow-x:auto}}
.aba-btn{{background:none;border:none;padding:14px 18px;font-size:12.5px;font-weight:600;color:var(--muted);cursor:pointer;border-bottom:3px solid transparent;white-space:nowrap;transition:.15s}}
.aba-btn:hover{{color:var(--navy)}}
.aba-btn.ativo{{color:var(--teal);border-bottom-color:var(--teal)}}
.badge{{background:var(--bg);color:var(--muted);border-radius:20px;padding:1px 8px;font-size:10.5px;margin-left:4px}}
.aba-btn.ativo .badge{{background:var(--teal);color:#fff}}

main{{padding:24px 28px}}

.aba-conteudo{{display:none}}
.aba-conteudo.ativo{{display:block}}
.fonte-info{{font-size:11.5px;color:var(--muted);margin-bottom:14px;background:#fff;border:1px solid var(--border);border-radius:6px;padding:8px 12px;display:inline-block}}

.resumo{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:18px}}
.card{{background:#fff;border:1px solid var(--border);border-radius:var(--radius);padding:12px 16px;min-width:120px;box-shadow:var(--shadow)}}
.card-total{{background:var(--navy);color:#fff;border-color:var(--navy)}}
.card-num{{font-size:20px;font-weight:800;color:var(--navy)}}
.card-total .card-num{{color:#fff}}
.card-label{{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;margin-top:2px}}
.card-total .card-label{{color:#c9d6ee}}
.resumo-vazio{{color:var(--muted);font-style:italic;padding:20px;background:#fff;border-radius:var(--radius);border:1px solid var(--border)}}
.resumo-vinculo{{margin-bottom:6px}}
.card-gdf{{border-color:var(--teal)}}
.card-gdf .card-num{{color:var(--teal)}}
.card-priv{{border-color:#c98a1f}}
.card-priv .card-num{{color:#c98a1f}}

.chips{{display:flex;gap:8px;margin-bottom:12px}}
.chip{{background:#fff;border:1.5px solid var(--border);border-radius:20px;padding:6px 14px;font-size:11.5px;font-weight:600;color:var(--muted);cursor:pointer;transition:.15s}}
.chip:hover{{border-color:var(--teal)}}
.chip.ativo{{background:var(--teal);border-color:var(--teal);color:#fff}}

.painel-busca{{display:flex;align-items:center;gap:12px;margin-bottom:10px}}
.painel-busca input{{flex:1;max-width:400px;border:1.5px solid var(--border);border-radius:6px;padding:8px 12px;font-size:12.5px}}
.painel-busca input:focus{{outline:none;border-color:var(--teal);box-shadow:0 0 0 3px rgba(0,144,168,.12)}}
.contador{{font-size:11.5px;color:var(--muted)}}

.tabela-wrap{{background:#fff;border-radius:var(--radius);border:1px solid var(--border);overflow:auto;max-height:70vh;box-shadow:var(--shadow)}}
table{{border-collapse:collapse;width:100%;font-size:12px}}
thead{{position:sticky;top:0;background:var(--navy-mid);color:#fff;z-index:5}}
th{{padding:9px 12px;text-align:left;font-weight:600;white-space:nowrap;cursor:pointer;user-select:none}}
th:hover{{background:var(--navy-light)}}
td{{padding:7px 12px;border-bottom:1px solid var(--border);white-space:nowrap;max-width:320px;overflow:hidden;text-overflow:ellipsis}}
tbody tr:nth-child(even){{background:var(--row-alt)}}
tbody tr:hover{{background:var(--hover)}}

.rodape-nota{{margin-top:24px;font-size:11px;color:var(--muted);background:#fff;border:1px solid var(--border);border-radius:var(--radius);padding:14px 16px}}
.rodape-nota code{{background:var(--bg);padding:1px 5px;border-radius:4px;font-size:10.5px}}
</style>
</head>
<body>
<header>
  <div>
    <h1>TransfereGov × Distrito Federal <span>Visão consolidada — propostas, convênios/parcerias, situação e valores</span></h1>
  </div>
  <div id="ts">Gerado em {data_geracao}</div>
</header>

<nav class="abas-nav">
  {abas_nav}
</nav>

<script>
window.DADOS = {{}};
window.COLUNAS = {{}};
window.ORDEM = {{}};
</script>

<main>
  {abas_conteudo}

  <div class="rodape-nota">
    <strong>Dados abertos</strong> — origem: <code>https://api-publica.transferegov.gestao.gov.br/</code>.
    A coluna <strong>Vínculo GDF</strong> classifica cada registro comparando o CNPJ do ente recebedor/proponente/beneficiário
    com a lista de 161 CNPJs de órgãos e entidades do GDF (extraída de <code>MIL2026.UNIDADEGESTORA</code>, campo <code>NUCGC</code>),
    mais o CNPJ do próprio ente federativo Distrito Federal (00.394.601/0001-26). Registros marcados como
    "Privado/Outro" estão apenas fisicamente sediados no DF (associações, cooperativas, empresas), sem relação
    com o governo distrital. Este painel é uma extração bruta para análise exploratória; ainda não foi cruzado com o SIGGO.
  </div>
</main>

<script>
const DADOS = window.DADOS;
const COLUNAS = window.COLUNAS;
const ORDEM = window.ORDEM;
const VINCULO_ATIVO = {{}};

function mostrarAba(id) {{
  document.querySelectorAll('.aba-conteudo').forEach(el => el.classList.remove('ativo'));
  document.querySelectorAll('.aba-btn').forEach(el => el.classList.remove('ativo'));
  document.getElementById('conteudo-' + id).classList.add('ativo');
  document.querySelector(`.aba-btn[data-aba="${{id}}"]`).classList.add('ativo');
  if (!ORDEM[id]) renderizar(id, DADOS[id]);
}}

function renderizar(id, linhas) {{
  const tbody = document.querySelector(`#tabela-${{id}} tbody`);
  const cols = COLUNAS[id];
  tbody.innerHTML = linhas.map(row =>
    '<tr>' + cols.map(c => `<td title="${{String(row[c]).replace(/"/g,'&quot;')}}">${{row[c]}}</td>`).join('') + '</tr>'
  ).join('');
  document.getElementById('contador-' + id).textContent = linhas.length + ' registros';
}}

function filtrar(id) {{
  const termo = document.getElementById('busca-' + id).value.toLowerCase();
  const vinculo = VINCULO_ATIVO[id] || '';
  const linhas = DADOS[id].filter(row =>
    (vinculo === '' || row['Vínculo GDF'] === vinculo) &&
    Object.values(row).some(v => String(v).toLowerCase().includes(termo))
  );
  renderizar(id, linhas);
}}

function filtrarVinculo(id, valor, botao) {{
  VINCULO_ATIVO[id] = valor;
  document.querySelectorAll(`#chips-${{id}} .chip`).forEach(el => el.classList.remove('ativo'));
  botao.classList.add('ativo');
  filtrar(id);
}}

function ordenar(id, colIdx) {{
  const cols = COLUNAS[id];
  const chave = cols[colIdx];
  ORDEM[id] = ORDEM[id] === chave ? null : chave;
  const asc = ORDEM[id] === chave;
  const linhas = [...DADOS[id]].sort((a, b) => {{
    let va = a[chave], vb = b[chave];
    const na = parseFloat(String(va).replace(/\\./g,'').replace(',','.'));
    const nb = parseFloat(String(vb).replace(/\\./g,'').replace(',','.'));
    if (!isNaN(na) && !isNaN(nb)) {{ va = na; vb = nb; }}
    if (va < vb) return asc ? -1 : 1;
    if (va > vb) return asc ? 1 : -1;
    return 0;
  }});
  renderizar(id, linhas);
}}

// Ativa a primeira aba
document.addEventListener('DOMContentLoaded', () => {{
  const primeira = document.querySelector('.aba-btn').dataset.aba;
  renderizar(primeira, DADOS[primeira]);
}});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
