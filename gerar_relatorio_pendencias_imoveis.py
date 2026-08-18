"""
Relatório de Pendências – Conciliação Bens Imóveis (SIGGO × SISGEPAT)
======================================================================
Gera um Excel de decisão com as pendências identificadas na conciliação,
usando exatamente a mesma extração e regras do painel HTML
conciliacao_bens_imoveis_sisgepat.html.

Uso:
    python gerar_relatorio_pendencias_imoveis.py
    python gerar_relatorio_pendencias_imoveis.py --mes 7 --ano 2026
"""

import argparse
import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import openpyxl
from openpyxl.styles import (Alignment, Border, Font, GradientFill,
                              PatternFill, Side)
from openpyxl.utils import get_column_letter
from dotenv import load_dotenv

# ── Credenciais (mesmo .env do HTML) ─────────────────────────────────────────
_PASTA = Path(__file__).parent
load_dotenv(_PASTA / ".env")

# ── Carrega o script do painel HTML (contém extrair() já com filtro correto) ─
_HTML_SCRIPT = _PASTA / "extrair_conciliacao_bens_imoveis_sisgepat.py"
_spec = importlib.util.spec_from_file_location("painel_imoveis", str(_HTML_SCRIPT))
_painel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_painel)

MESES = _painel.MESES
_core = _painel._core   # módulo core (conciliacao_siggo_sisgepat.py)

# ── Paleta de cores (alinhada ao painel HTML) ─────────────────────────────────
def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)

NAVY      = _fill("0D1B3E")   # cabeçalho título
NAVY_MID  = _fill("162550")   # cabeçalho coluna
TEAL      = _fill("0090A8")   # destaques / totais
TEAL_SOFT = _fill("E0F4F7")   # alternada par
WHITE     = _fill("FFFFFF")
RED_SOFT  = _fill("FDECEA")
AMBER     = _fill("FFF8E1")
GRAY      = _fill("F2F5F9")
GREEN_SOFT= _fill("E8F5E9")

WHITE_FONT = Font(name="Calibri", color="FFFFFF", bold=True, size=10)
HDR_FONT   = Font(name="Calibri", color="FFFFFF", bold=True, size=9)
BODY_FONT  = Font(name="Calibri", size=9)
BOLD_FONT  = Font(name="Calibri", size=9, bold=True)
TITLE_FONT = Font(name="Calibri", color="FFFFFF", bold=True, size=11)
TOTAL_FONT = Font(name="Calibri", color="FFFFFF", bold=True, size=9)

_thin = Side(style="thin", color="BCC8D8")
_med  = Side(style="medium", color="8FA3BB")
BORDER_THIN = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
BORDER_MED  = Border(left=_med,  right=_med,  top=_med,  bottom=_med)

FMT_BRL  = '#,##0.00'
FMT_INT  = '#,##0'
CENTER   = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT     = Alignment(horizontal="left",   vertical="center", wrap_text=True)
RIGHT    = Alignment(horizontal="right",  vertical="center")


def _c(ws, row, col, value=None, *, font=None, fill=None, fmt=None,
       align=None, border=BORDER_THIN, bold=False, size=9, color=None):
    """Escreve célula com formatação."""
    cell = ws.cell(row=row, column=col, value=value)
    if font:
        cell.font = font
    else:
        f = Font(name="Calibri", size=size, bold=bold)
        if color:
            f = Font(name="Calibri", size=size, bold=bold, color=color)
        cell.font = f
    if fill:
        cell.fill = fill
    if fmt:
        cell.number_format = fmt
    cell.alignment = align or LEFT
    if border:
        cell.border = border
    return cell


def _auto_width(ws, min_w=8, max_w=55):
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                val = str(cell.value) if cell.value is not None else ""
                max_len = max(max_len, len(val))
            except Exception:
                pass
        ws.column_dimensions[col_letter].width = max(min_w, min(max_len + 2, max_w))


def _titulo_aba(ws, texto, ncols, subtexto=None):
    """Faixa de título da aba (linhas 1-2 ou 1-3)."""
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    _c(ws, 1, 1, texto, fill=NAVY, font=TITLE_FONT, align=CENTER, border=BORDER_MED)
    ws.row_dimensions[1].height = 22
    r = 2
    if subtexto:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
        _c(ws, r, 1, subtexto, fill=NAVY_MID, font=Font(name="Calibri", color="C0CFDF", size=8, italic=True),
           align=CENTER, border=BORDER_THIN)
        ws.row_dimensions[r].height = 14
        r += 1
    return r   # primeira linha livre


def _cabecalho(ws, row, headers):
    for j, h in enumerate(headers, 1):
        _c(ws, row, j, h, fill=NAVY_MID, font=HDR_FONT, align=CENTER)
    ws.row_dimensions[row].height = 30


def _total_row(ws, row, ncols, label, valores, label_cols=(1, 2)):
    """Linha de total: label mesclada, valores nas colunas indicadas."""
    ws.merge_cells(start_row=row, start_column=label_cols[0],
                   end_row=row, end_column=label_cols[1])
    _c(ws, row, label_cols[0], label, fill=TEAL, font=TOTAL_FONT,
       align=CENTER, border=BORDER_MED)
    for col, val in valores:
        _c(ws, row, col, val, fill=TEAL, font=TOTAL_FONT, fmt=FMT_BRL,
           align=RIGHT, border=BORDER_MED)


# ─────────────────────────────────────────────────────────────────────────────
#  ABA 1 – PAINEL DE PENDÊNCIAS (resumo executivo)
# ─────────────────────────────────────────────────────────────────────────────
def _aba_painel(wb, quadro, eventos, transf, siggo_tomb, mes, ano, achados):
    ws = wb.create_sheet("Painel de Pendências")
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A4"

    mes_label = MESES[mes]
    titulo = (f"CONCILIAÇÃO PATRIMONIAL SIGGO × SISGEPAT – BENS IMÓVEIS  |  "
              f"{mes_label}/{ano}  |  Emitido em {datetime.now():%d/%m/%Y %H:%M}")
    sub = ("Relatório de pendências para tomada de decisão – Administração Direta e Fundos do GDF  |  "
           "Contas conciliáveis: TERRENOS · PRÉDIOS · MOBILIÁRIO URBANO · BENS IMÓVEIS A REGULARIZAR · OBRAS EM ANDAMENTO")

    r = _titulo_aba(ws, titulo, 6, sub)

    # ── Totalizadores conciliação ────────────────────────────────────────────
    q_concil = quadro[(quadro["TIPO_UG"] == "Direta") & (quadro["CATEGORIA"] == "SISGEPAT")]
    tot_sis   = q_concil["SISGEPAT_VALOR"].sum()
    tot_sig   = q_concil["SIGGO_VALOR"].sum()
    dif_concil = q_concil["DIFERENCA"].sum()
    n_ug_tot   = q_concil["UG"].nunique()
    n_ug_ok    = q_concil.groupby("UG")["DIFERENCA"].sum().abs().lt(0.01).sum()

    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    _c(ws, r, 1, "SITUAÇÃO GERAL DA CONCILIAÇÃO (Administração Direta – contas conciliáveis)",
       fill=TEAL, font=Font(name="Calibri", color="FFFFFF", bold=True, size=9),
       align=CENTER, border=BORDER_MED)
    r += 1

    kpi_data = [
        ("SISGEPAT (R$)",      tot_sis,        "Total inventariado no SISGEPAT"),
        ("SIGGO (R$)",         tot_sig,        "Total registrado no SIGGO"),
        ("Diferença (R$)",     dif_concil,     "SISGEPAT − SIGGO (deve tender a zero)"),
        ("UGs conciliadas",    int(n_ug_ok),   f"de {n_ug_tot} UGs da Adm. Direta"),
    ]
    _cabecalho(ws, r, ["Indicador", "Valor", "Observação", "", "", ""])
    r += 1
    for label, val, obs in kpi_data:
        fill = GREEN_SOFT if ("Diferença" not in label or abs(val) < 0.01) else RED_SOFT
        if "UGs" in label:
            fill = GREEN_SOFT if int(val) == n_ug_tot else AMBER
        _c(ws, r, 1, label, fill=fill, bold=True, border=BORDER_THIN)
        if isinstance(val, int):
            _c(ws, r, 2, val, fill=fill, fmt=FMT_INT, align=RIGHT, border=BORDER_THIN)
        else:
            _c(ws, r, 2, val, fill=fill, fmt=FMT_BRL, align=RIGHT, border=BORDER_THIN,
               color="C0392B" if ("Diferença" in label and abs(val) > 0.01) else None)
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=6)
        _c(ws, r, 3, obs, fill=fill, border=BORDER_THIN)
        r += 1

    r += 1  # linha em branco

    # ── Resumo de pendências ─────────────────────────────────────────────────
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    _c(ws, r, 1, "PENDÊNCIAS IDENTIFICADAS – ação necessária pela unidade gestora",
       fill=TEAL, font=Font(name="Calibri", color="FFFFFF", bold=True, size=9),
       align=CENTER, border=BORDER_MED)
    r += 1
    _cabecalho(ws, r, ["Tipo de Pendência", "Qtde", "Valor Envolvido (R$)", "Aba de Detalhamento", "Providência", ""])
    r += 1

    # Transferências
    n_transf = len(transf) if transf is not None else 0
    vl_transf = float(transf["SIGGO_VALOR"].sum()) if (transf is not None and len(transf)) else 0.0
    # Eventos
    n_ev = len(eventos) if eventos is not None else 0
    vl_ev = float(eventos["SIGGO_VALOR"].abs().sum()) if (eventos is not None and len(eventos)) else 0.0
    # Inscrições genéricas (contas conciliáveis)
    ger = siggo_tomb.attrs.get("generico") if hasattr(siggo_tomb, "attrs") else None
    n_ig = vl_ig = 0
    if ger is not None and len(ger):
        ig_concil = ger[ger["CONTA_CONTABIL"].isin(_core.CONTAS_SISGEPAT)] if "CONTA_CONTABIL" in ger.columns else ger
        n_ig = len(ig_concil)
        vl_ig = float(ig_concil["VALOR"].sum()) if "VALOR" in ig_concil.columns else 0.0
    # Divergências
    q_div = quadro[(quadro["TIPO_UG"] == "Direta") & (quadro["CATEGORIA"] == "SISGEPAT")
                   & (quadro["DIFERENCA"].abs() > 0.01)]
    n_div = len(q_div)
    vl_div = float(q_div["DIFERENCA"].abs().sum())

    pend_rows = [
        ("Transferências de imóveis sem evento SIGGO", n_transf, vl_transf,
         "Transferências Pendentes",
         "Registrar evento de transferência no SIGGO (Adm. Direta: dispensa doação; Indireta → Direta: exige doação)"),
        ("Eventos contábeis pendentes (reclassificação de conta)", n_ev, vl_ev,
         "Eventos Pendentes",
         "Lançar evento de transferência de conta no SIGGO (art. 7.º do Dec. 16.109/94)"),
        ("Saldos em inscrição genérica (sem tombamento individualizado)", n_ig, vl_ig,
         "Inscrições Genéricas",
         "Reclassificar para inscrição individualizada por tombamento (CI + nº de tombamento)"),
        ("Divergências sem explicação identificada", n_div, vl_div,
         "Divergências a Investigar",
         "Investigar por UG e adotar a providência cabível (regularização documental, reclassificação ou contestação)"),
    ]

    fills_alt = [WHITE, GRAY]
    for i, (desc, qtde, valor, aba, prov) in enumerate(pend_rows):
        f = fills_alt[i % 2]
        _c(ws, r, 1, desc, fill=f, bold=True, border=BORDER_THIN)
        _c(ws, r, 2, qtde, fill=f, fmt=FMT_INT, align=RIGHT, border=BORDER_THIN)
        _c(ws, r, 3, valor, fill=f, fmt=FMT_BRL, align=RIGHT, border=BORDER_THIN)
        _c(ws, r, 4, aba, fill=f, align=CENTER, border=BORDER_THIN,
           color="0090A8", bold=True)
        ws.merge_cells(start_row=r, start_column=5, end_row=r, end_column=6)
        _c(ws, r, 5, prov, fill=f, border=BORDER_THIN)
        ws.row_dimensions[r].height = 30
        r += 1

    r += 1
    # Nota de rodapé
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    nota = ("NOTA: Este relatório usa os mesmos critérios e fonte de dados do painel "
            "conciliacao_bens_imoveis_sisgepat.html. Inclui apenas Administração Direta e Fundos "
            "(INTIPOADM = 1 e 7), excluindo Câmara Legislativa. Diferença = SIGGO − SISGEPAT.")
    _c(ws, r, 1, nota, fill=GRAY, font=Font(name="Calibri", size=8, italic=True, color="6B7A99"),
       align=LEFT, border=Border())

    ws.column_dimensions["A"].width = 48
    ws.column_dimensions["B"].width = 9
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 26
    ws.column_dimensions["E"].width = 45
    ws.column_dimensions["F"].width = 10


# ─────────────────────────────────────────────────────────────────────────────
#  ABA 2 – TRANSFERÊNCIAS PENDENTES
# ─────────────────────────────────────────────────────────────────────────────
def _aba_transferencias(wb, transf):
    ws = wb.create_sheet("Transferências Pendentes")
    ws.sheet_view.showGridLines = False

    sub = ("Imóveis que o SISGEPAT já registra em uma unidade e o SIGGO mantém em outra. "
           "A UG de ORIGEM fica A MAIOR e a de DESTINO fica A MENOR até que o evento seja lançado.")
    r = _titulo_aba(ws, "TRANSFERÊNCIAS PENDENTES DE EVENTO NO SIGGO", 8, sub)

    headers = ["Tombamento", "UG Origem (SIGGO)", "Nome da UG de Origem",
               "UG Destino (SISGEPAT)", "Nome da UG de Destino",
               "Tipo", "Valor no SIGGO (R$)", "Providência"]
    _cabecalho(ws, r, headers)
    ws.freeze_panes = f"A{r + 1}"
    r += 1

    if transf is None or len(transf) == 0:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        _c(ws, r, 1, "Nenhuma transferência pendente identificada.", fill=GREEN_SOFT,
           align=CENTER, border=BORDER_THIN)
        return

    fills_alt = [WHITE, TEAL_SOFT]
    ug_ant = None
    vl_total = 0.0

    for i, row_data in enumerate(transf.itertuples(index=False)):
        tombamento = getattr(row_data, "TOMB_NORM", "")
        ug_orig    = getattr(row_data, "UG_SIGGO", "")
        nome_orig  = getattr(row_data, "NOME_UG_SIGGO", "")
        ug_dest    = getattr(row_data, "UG_SISGEPAT", "")
        nome_dest  = getattr(row_data, "NOME_UG_SISGEPAT", "")
        tipo       = getattr(row_data, "TIPO_ORIGEM", "")
        valor      = float(getattr(row_data, "SIGGO_VALOR", 0) or 0)
        prov       = getattr(row_data, "PROVIDENCIA", "")

        if not prov or str(prov).strip() == "nan":
            prov = "Registrar evento de transferência no SIGGO."

        f = fills_alt[i % 2]
        vl_total += valor
        _c(ws, r, 1, tombamento,  fill=f, align=CENTER, border=BORDER_THIN)
        _c(ws, r, 2, ug_orig,     fill=f, align=CENTER, border=BORDER_THIN)
        _c(ws, r, 3, nome_orig,   fill=f, border=BORDER_THIN)
        _c(ws, r, 4, ug_dest,     fill=f, align=CENTER, border=BORDER_THIN)
        _c(ws, r, 5, nome_dest,   fill=f, border=BORDER_THIN)
        _c(ws, r, 6, tipo,        fill=f, align=CENTER, border=BORDER_THIN)
        _c(ws, r, 7, valor,       fill=f, fmt=FMT_BRL, align=RIGHT, border=BORDER_THIN)
        _c(ws, r, 8, prov,        fill=f, border=BORDER_THIN)
        ws.row_dimensions[r].height = 30
        r += 1

    _total_row(ws, r, 8, f"TOTAL – {len(transf)} imóvel(is)",
               [(7, vl_total)], label_cols=(1, 6))

    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 38
    ws.column_dimensions["D"].width = 12
    ws.column_dimensions["E"].width = 38
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 20
    ws.column_dimensions["H"].width = 55


# ─────────────────────────────────────────────────────────────────────────────
#  ABA 3 – EVENTOS CONTÁBEIS PENDENTES
# ─────────────────────────────────────────────────────────────────────────────
def _aba_eventos(wb, eventos):
    ws = wb.create_sheet("Eventos Pendentes")
    ws.sheet_view.showGridLines = False

    sub = ("Imóveis classificados em conta diferente nos dois sistemas. "
           "A unidade gestora precisa lançar o evento contábil de reclassificação no SIGGO.")
    r = _titulo_aba(ws, "EVENTOS CONTÁBEIS PENDENTES NO SIGGO – por imóvel (tombamento)", 8, sub)

    headers = ["UG", "Unidade Gestora", "Tombamento",
               "Conta Atual no SIGGO", "Conta Devida (SISGEPAT)",
               "Valor SIGGO (R$)", "Valor SISGEPAT (R$)", "Providência"]
    _cabecalho(ws, r, headers)
    ws.freeze_panes = f"A{r + 1}"
    r += 1

    if eventos is None or len(eventos) == 0:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)
        _c(ws, r, 1, "Nenhum evento contábil pendente identificado.", fill=GREEN_SOFT,
           align=CENTER, border=BORDER_THIN)
        return

    fills_alt = [WHITE, TEAL_SOFT]
    ug_atual = None
    totais = {"sig": 0.0, "sis": 0.0}
    n_total = 0
    ug_block_start = r

    # Agrupa por UG para subtotais
    grupos = eventos.groupby("UG", sort=False)

    for ug, grp in grupos:
        nome_ug = grp["NOME_UG"].iloc[0] if "NOME_UG" in grp.columns else ""
        bloco_start = r

        for i, row_data in enumerate(grp.itertuples(index=False)):
            tomb    = getattr(row_data, "TOMB_NORM", "")
            c_siggo = getattr(row_data, "CONTA_SIGGO", "")
            c_sis   = getattr(row_data, "CONTA_SISGEPAT", "")
            v_sig   = float(getattr(row_data, "SIGGO_VALOR", 0) or 0)
            v_sis   = float(getattr(row_data, "SISGEPAT_VALOR", 0) or 0)
            prov    = getattr(row_data, "EVENTO", "")

            f = fills_alt[n_total % 2]
            _c(ws, r, 1, ug,      fill=f, align=CENTER, border=BORDER_THIN)
            _c(ws, r, 2, nome_ug, fill=f, border=BORDER_THIN)
            _c(ws, r, 3, tomb,    fill=f, align=CENTER, border=BORDER_THIN)
            _c(ws, r, 4, c_siggo, fill=f, border=BORDER_THIN)
            _c(ws, r, 5, c_sis,   fill=f, border=BORDER_THIN)
            _c(ws, r, 6, v_sig,   fill=f, fmt=FMT_BRL, align=RIGHT, border=BORDER_THIN)
            _c(ws, r, 7, v_sis,   fill=f, fmt=FMT_BRL, align=RIGHT, border=BORDER_THIN)
            _c(ws, r, 8, prov,    fill=f, border=BORDER_THIN)
            ws.row_dimensions[r].height = 30
            totais["sig"] += v_sig
            totais["sis"] += v_sis
            n_total += 1
            r += 1

        # Subtotal por UG
        sub_sig = grp["SIGGO_VALOR"].sum() if "SIGGO_VALOR" in grp.columns else 0
        sub_sis = grp["SISGEPAT_VALOR"].sum() if "SISGEPAT_VALOR" in grp.columns else 0
        label = f"Subtotal {ug} – {len(grp)} imóvel(is)"
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
        _c(ws, r, 1, label, fill=_fill("D0E8EF"), font=Font(name="Calibri", size=9, bold=True),
           align=CENTER, border=BORDER_MED)
        _c(ws, r, 6, float(sub_sig), fill=_fill("D0E8EF"),
           font=Font(name="Calibri", size=9, bold=True), fmt=FMT_BRL, align=RIGHT, border=BORDER_MED)
        _c(ws, r, 7, float(sub_sis), fill=_fill("D0E8EF"),
           font=Font(name="Calibri", size=9, bold=True), fmt=FMT_BRL, align=RIGHT, border=BORDER_MED)
        _c(ws, r, 8, "", fill=_fill("D0E8EF"), border=BORDER_MED)
        r += 1

    _total_row(ws, r, 8, f"TOTAL GERAL – {n_total} imóvel(is)",
               [(6, totais["sig"]), (7, totais["sis"])], label_cols=(1, 5))


    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 35
    ws.column_dimensions["E"].width = 35
    ws.column_dimensions["F"].width = 20
    ws.column_dimensions["G"].width = 20
    ws.column_dimensions["H"].width = 55


# ─────────────────────────────────────────────────────────────────────────────
#  ABA 4 – INSCRIÇÕES GENÉRICAS
# ─────────────────────────────────────────────────────────────────────────────
def _aba_inscricoes_genericas(wb, siggo_tomb):
    ws = wb.create_sheet("Inscrições Genéricas")
    ws.sheet_view.showGridLines = False

    sub = ("Saldos registrados no SIGGO sob inscrição genérica (ex.: IM9999999) sem tombamento "
           "individualizado. Enquanto não reclassificados, a conciliação imóvel a imóvel é impossível.")
    r = _titulo_aba(ws, "SALDOS DO SIGGO EM INSCRIÇÃO GENÉRICA (sem tombamento)", 6, sub)

    headers = ["UG", "Unidade Gestora", "Conta Contábil", "Descrição da Conta",
               "Conciliável com SISGEPAT?", "Valor (R$)"]
    _cabecalho(ws, r, headers)
    ws.freeze_panes = f"A{r + 1}"
    r += 1

    ger = siggo_tomb.attrs.get("generico") if hasattr(siggo_tomb, "attrs") else None

    if ger is None or len(ger) == 0:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        _c(ws, r, 1, "Nenhuma inscrição genérica identificada.", fill=GREEN_SOFT,
           align=CENTER, border=BORDER_THIN)
        return

    # Só contas conciliáveis (o usuário precisa agir nestas)
    conciliaveis = ger[ger["CONTA_CONTABIL"].isin(_core.CONTAS_SISGEPAT)].copy() \
        if "CONTA_CONTABIL" in ger.columns else ger.copy()

    all_rows = ger.copy()
    if "CONCILIAVEL" not in all_rows.columns:
        all_rows["CONCILIAVEL"] = all_rows["CONTA_CONTABIL"].isin(_core.CONTAS_SISGEPAT).map(
            {True: "SIM", False: "NÃO"})

    fills_alt = [WHITE, TEAL_SOFT]
    vl_concil = vl_nao = 0.0

    # Ordena: conciliáveis primeiro, depois por UG e valor desc
    all_rows = all_rows.sort_values(
        ["CONCILIAVEL", "COD_UG", "VALOR"],
        ascending=[False, True, False])

    for i, row_data in enumerate(all_rows.itertuples(index=False)):
        ug       = str(getattr(row_data, "COD_UG", "")).zfill(6)
        nome_ug  = getattr(row_data, "NOME_UG", "")
        conta    = getattr(row_data, "CONTA_CONTABIL", "")
        desc     = _core.DESCRICAO_CONTA.get(str(conta), getattr(row_data, "DESCRICAO_CONTA", ""))
        conc     = getattr(row_data, "CONCILIAVEL", "")
        valor    = float(getattr(row_data, "VALOR", 0) or 0)

        f = RED_SOFT if conc == "SIM" else GRAY
        if i % 2 == 0 and conc != "SIM":
            f = WHITE
        _c(ws, r, 1, ug,    fill=f, align=CENTER, border=BORDER_THIN)
        _c(ws, r, 2, nome_ug, fill=f, border=BORDER_THIN)
        _c(ws, r, 3, conta, fill=f, align=CENTER, border=BORDER_THIN)
        _c(ws, r, 4, desc,  fill=f, border=BORDER_THIN)
        _c(ws, r, 5, conc,  fill=f, align=CENTER, border=BORDER_THIN,
           bold=(conc == "SIM"), color="C0392B" if conc == "SIM" else None)
        _c(ws, r, 6, valor, fill=f, fmt=FMT_BRL, align=RIGHT, border=BORDER_THIN)

        if conc == "SIM":
            vl_concil += valor
        else:
            vl_nao += valor
        r += 1

    r += 1
    # Legenda
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    _c(ws, r, 1, "⚠ Linhas em vermelho claro (SIM): saldos em contas conciliáveis com o SISGEPAT — "
       "reclassificar para tombamento individualizado é PRIORITÁRIO para fechar a conciliação.",
       fill=RED_SOFT, font=Font(name="Calibri", size=8, italic=True, color="C0392B"),
       align=LEFT, border=Border())
    r += 1
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=5)
    _c(ws, r, 1, "TOTAL em inscrições genéricas CONCILIÁVEIS (ação prioritária)",
       fill=TEAL, font=TOTAL_FONT, align=CENTER, border=BORDER_MED)
    _c(ws, r, 6, vl_concil, fill=TEAL, font=TOTAL_FONT, fmt=FMT_BRL,
       align=RIGHT, border=BORDER_MED)

    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 42
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 38
    ws.column_dimensions["E"].width = 22
    ws.column_dimensions["F"].width = 20


# ─────────────────────────────────────────────────────────────────────────────
#  ABA 5 – DIVERGÊNCIAS A INVESTIGAR
# ─────────────────────────────────────────────────────────────────────────────
def _aba_divergencias(wb, quadro):
    ws = wb.create_sheet("Divergências a Investigar")
    ws.sheet_view.showGridLines = False

    sub = ("Combinações UG + conta contábil com diferença entre SIGGO e SISGEPAT, "
           "ordenadas pelo valor absoluto da divergência (maiores primeiro). "
           "Diferença = SIGGO − SISGEPAT.")
    r = _titulo_aba(ws, "DIVERGÊNCIAS A INVESTIGAR – Administração Direta (contas conciliáveis)", 7, sub)

    headers = ["UG", "Unidade Gestora", "Conta", "Descrição da Conta",
               "SISGEPAT (R$)", "SIGGO (R$)", "Diferença (R$)"]
    _cabecalho(ws, r, headers)
    ws.freeze_panes = f"A{r + 1}"
    r += 1

    q_div = (quadro[(quadro["TIPO_UG"] == "Direta")
                    & (quadro["CATEGORIA"] == "SISGEPAT")
                    & (quadro["DIFERENCA"].abs() > 0.01)]
             .copy())
    q_div = q_div.sort_values("DIFERENCA", key=lambda s: s.abs(), ascending=False)

    if len(q_div) == 0:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
        _c(ws, r, 1, "Nenhuma divergência identificada — conciliação dentro dos limites.", fill=GREEN_SOFT,
           align=CENTER, border=BORDER_THIN)
        return

    fills_alt = [WHITE, TEAL_SOFT]
    for i, row_data in enumerate(q_div.itertuples(index=False)):
        ug   = getattr(row_data, "UG", "")
        nome = getattr(row_data, "NOME_UG", "")
        conta= getattr(row_data, "CONTA", "")
        desc = getattr(row_data, "DESCRICAO_CONTA", "")
        sis  = float(getattr(row_data, "SISGEPAT_VALOR", 0) or 0)
        sig  = float(getattr(row_data, "SIGGO_VALOR", 0) or 0)
        dif  = float(getattr(row_data, "DIFERENCA", 0) or 0)

        f = fills_alt[i % 2]
        dif_fill = RED_SOFT if dif < -0.01 else (AMBER if dif > 0.01 else f)

        _c(ws, r, 1, ug,   fill=f, align=CENTER, border=BORDER_THIN)
        _c(ws, r, 2, nome, fill=f, border=BORDER_THIN)
        _c(ws, r, 3, conta,fill=f, align=CENTER, border=BORDER_THIN)
        _c(ws, r, 4, desc, fill=f, border=BORDER_THIN)
        _c(ws, r, 5, sis,  fill=f, fmt=FMT_BRL, align=RIGHT, border=BORDER_THIN)
        _c(ws, r, 6, sig,  fill=f, fmt=FMT_BRL, align=RIGHT, border=BORDER_THIN)
        _c(ws, r, 7, dif,  fill=dif_fill, fmt=FMT_BRL, align=RIGHT, border=BORDER_THIN,
           bold=True, color="C0392B" if dif < -0.01 else ("856D00" if dif > 0.01 else None))
        r += 1

    _total_row(ws, r, 7,
               f"TOTAL – {len(q_div)} combinação(ões) UG+conta com divergência",
               [(5, float(q_div["SISGEPAT_VALOR"].sum())),
                (6, float(q_div["SIGGO_VALOR"].sum())),
                (7, float(q_div["DIFERENCA"].sum()))],
               label_cols=(1, 4))

    r += 2
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=7)
    _c(ws, r, 1,
       "Legenda de cores – Diferença:  🔴 vermelho claro = SIGGO menor que SISGEPAT (A MENOR no SIGGO)  |  "
       "🟡 amarelo = SIGGO maior que SISGEPAT (A MAIOR no SIGGO)",
       fill=GRAY, font=Font(name="Calibri", size=8, italic=True, color="6B7A99"),
       align=LEFT, border=Border())

    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 42
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 35
    ws.column_dimensions["E"].width = 20
    ws.column_dimensions["F"].width = 20
    ws.column_dimensions["G"].width = 20


# ─────────────────────────────────────────────────────────────────────────────
#  GERAÇÃO DO ARQUIVO
# ─────────────────────────────────────────────────────────────────────────────
def gerar(mes, ano):
    print(f"\n{'='*60}")
    print(f"  RELATÓRIO DE PENDÊNCIAS – BENS IMÓVEIS  {MESES[mes]}/{ano}")
    print(f"{'='*60}\n")

    print("[1/3] Extraindo dados (mesma fonte do painel HTML)…")
    quadro, eventos, transf, achados, siggo_tomb = _painel.extrair(mes, ano)
    print(f"  Quadro: {len(quadro)} linhas  |  Eventos: {len(eventos) if eventos is not None else 0}"
          f"  |  Transferências: {len(transf) if transf is not None else 0}")

    print("[2/3] Montando relatório Excel…")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)   # remove planilha padrão vazia

    _aba_painel(wb, quadro, eventos, transf, siggo_tomb, mes, ano, achados)
    _aba_transferencias(wb, transf)
    _aba_eventos(wb, eventos)
    _aba_inscricoes_genericas(wb, siggo_tomb)
    _aba_divergencias(wb, quadro)

    print("[3/3] Salvando…")
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = f"Relatorio_Pendencias_Imoveis_{ano}_{mes:02d}_{ts}.xlsx"
    saida = _PASTA / nome
    wb.save(str(saida))
    print(f"\n  ✔ Arquivo salvo: {saida}\n")
    return saida


def main():
    p = argparse.ArgumentParser(description="Relatório de pendências – Bens Imóveis")
    p.add_argument("--mes", type=int, default=None)
    p.add_argument("--ano", type=int, default=None)
    a = p.parse_args()

    hoje = datetime.today()
    mes  = a.mes or (hoje.month - 1 or 12)
    ano  = a.ano or (hoje.year if hoje.month > 1 else hoje.year - 1)
    gerar(mes, ano)


if __name__ == "__main__":
    main()
