# Controles Contábeis — GDF

Painéis interativos de controles contábeis do Governo do Distrito Federal, publicados via GitHub Pages e alimentados por ETL automatizado a partir do banco Oracle (SIGGO/SISGEPAT).

**Acesso público:** https://controles-contabeis-df.github.io/controles-contabeis/

---

## Painéis disponíveis

| Painel | Arquivo HTML | Script ETL |
|--------|-------------|------------|
| Portal de navegação | `index.html` | — |
| Disponibilidades por Lançamento | `disponibilidades_lancamento.html` | `extrair_disponibilidades.py` |
| Disponibilidades por Saldo | `disponibilidades_saldo.html` | `extrair_disponibilidades_saldo.py` |
| Disponibilidade por Destinação de Recurso | `disponibilidade_destinacao_recurso.html` | `extrair_disponibilidade_destinacao_recurso.py` |
| DDR por Lançamento | `ddr_lancamento.html` | `extrair_ddr_lancamento.py` |
| Contas Transitórias | `contas_transitorias.html` | `extrair_contas_transitorias.py` |
| Empenhos a Liquidar | `empenhos_liquidar.html` | `extrair_empenhos_liquidar.py` |
| Repasses | `repasses.html` | `extrair_repasses.py` |
| Saldo Invertido | `saldo_invertido.html` | `extrair_saldo_invertido.py` |
| RPP por NE | `rpp_por_ne.html` | `extrair_rpp_por_ne.py` |
| Lançamentos Intragovernamentais | `intra_lancamento.html` | `extrair_intra_lancamento.py` |
| Conciliação de Bens Imóveis (Sisgepat/Siggo) | `conciliacao_bens_imoveis_sisgepat.html` | `extrair_conciliacao_bens_imoveis_sisgepat.py` |

---

## Alertas automáticos por e-mail

| Script | Finalidade |
|--------|-----------|
| `alerta_lancamentos_intra.py` | Divergências na Equação A (Classes 1+3 = 2+4, subtítulo 2) |
| `extrair_empresas_independentes.py` | Lançamentos em contas não orçamentárias de empresas independentes |

---

## Arquitetura

```
Oracle (SIGGO — rede interna GDF)
    └── scripts Python (extrair_*.py)
            └── HTML autocontido (dados embutidos via gzip+base64)
                    └── git push → GitHub Pages
                            └── alerta_lancamentos_intra.py → e-mail via Gmail API
```

A atualização roda automaticamente todos os dias às **11:00** via Agendador de Tarefas do Windows (`atualizar_paineis.ps1`).

---

## Configuração do ambiente

As credenciais Oracle e Gmail ficam exclusivamente no arquivo `.env` local (não versionado).

```
ORACLE_USER=...
ORACLE_PASS=...
ORACLE_DSN=...
```

Dependências Python:

```
pip install oracledb pandas python-dotenv google-auth google-auth-httplib2 google-api-python-client
```

---

## Responsável

**Clarissa Barbosa** — CONTDF / Secretaria de Estado de Economia — Distrito Federal
