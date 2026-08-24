# Controles Contábeis — SEEC/GDF

Painéis de controle contábil da Secretaria de Estado de Economia do Distrito Federal, publicados automaticamente como site estático no GitHub Pages a partir de dados extraídos do **SIGGO** (Sistema Integrado de Gestão Governamental e Orçamentária).

🔗 **Acesse o painel inicial:** https://controles-contabeis-df.github.io/controles-contabeis/

---

## Painéis disponíveis

| Painel | Descrição |
|--------|-----------|
| **Saldos Invertidos** | Identifica contas contábeis com saldo em sentido contrário ao esperado pela sua natureza devedora ou credora. |
| **Contas Transitórias** | Monitora contas de natureza transitória que deveriam estar zeradas ou com movimentação justificada. |
| **Repasse a Receber — Despesas Empenhadas e não Liquidadas** | Acompanha os valores empenhados e ainda não liquidados relacionados a repasses a receber. |
| **Disponibilidades por Saldo** | Verifica o equilíbrio entre as disponibilidades financeiras e os passivos exigíveis com base nos saldos contábeis acumulados. |
| **Disponibilidades por Lançamento** | Mesma análise das disponibilidades, por documento de lançamento contábil. |
| **DDR por Saldo** | Disponibilidade por Destinação de Recursos: confronta saldos orçamentários e financeiros por fonte de recurso. |
| **DDR por Lançamento** | Mesma análise do DDR, por documento de lançamento contábil. |
| **RPNP e RPP por Nota de Empenho** | Restos a Pagar Não Processados e Processados, detalhados por nota de empenho. |
| **Conciliação SIGGO e SISGEPAT — Bens Imóveis** | Concilia o patrimônio de bens imóveis registrado no SIGGO com o cadastro do SISGEPAT. |
| **Conciliação SIGGO e SISGEPAT — Bens Móveis** | Concilia o patrimônio de bens móveis registrado no SIGGO com o cadastro do SISGEPAT. |
| **Conciliação SIGGO e SISGEPAT — Bens Intangíveis** | Concilia os bens intangíveis registrados no SIGGO com o cadastro do SISGEPAT. |
| **Repasses e Subrepasses** | Monitora os repasses e subrepasses financeiros entre unidades gestoras do GDF. |
| **Correspondência por Lançamento — Ativo × Passivo** | Verifica equações de correspondência entre contas do Ativo e do Passivo (EQ1 a EQ7) por documento de lançamento contábil. |
| **Correspondência por Saldo — Ativo × Passivo** | Mesma análise de correspondência (EQ1 a EQ7) com base no saldo acumulado consolidado do GDF. |
| **Lançamentos Intragovernamentais** | Monitora os lançamentos intragovernamentais entre órgãos do GDF, verificando a correta contrapartida. |

---

## Como funciona

Cada painel é um arquivo `.html` autocontido — os dados do SIGGO são comprimidos e embutidos diretamente no HTML, sem necessidade de servidor ou banco de dados para a visualização. O acesso ao Oracle acontece apenas na etapa de extração, executada localmente.

```
Extração (Python + Oracle)  →  HTML autocontido  →  GitHub Pages (site público)
```

### Atualização dos painéis

A atualização ocorre automaticamente todo dia útil às **11h00** via Agendador de Tarefas do Windows, executando:

```powershell
.\atualizar_paineis.ps1
```

Para atualizar manualmente um painel específico:

```powershell
python extrair_<nome_do_painel>.py
```

---

## Requisitos técnicos (para quem for manter)

| Componente | Descrição |
|------------|-----------|
| **Python 3.13+** | Linguagem principal dos scripts de extração |
| **Oracle Instant Client 23** | Instalado em `C:\oracle\instantclient_23_0` |
| **oracledb** | Biblioteca Python para conexão Oracle (`pip install oracledb`) |
| **pandas** | Manipulação dos dados (`pip install pandas`) |
| **python-dotenv** | Leitura das credenciais (`pip install python-dotenv`) |
| **Arquivo `.env`** | Credenciais de acesso ao SIGGO/Oracle — **não versionado** |

### Estrutura do `.env`

O arquivo `.env` deve existir na mesma pasta dos scripts e conter:

```
ORACLE_USER=seu_usuario
ORACLE_PASS=sua_senha
ORACLE_DSN=host:porta/servico
```

> ⚠️ **O arquivo `.env` nunca deve ser versionado ou compartilhado.** Está listado no `.gitignore`.

---

## Estrutura do repositório

```
.
├── index.html                          ← Painel inicial (menu de navegação)
├── atualizar_paineis.ps1               ← Script de atualização em lote
├── extrair_*.py                        ← Scripts de extração (um por painel)
├── *.html                              ← Painéis gerados (publicados no GitHub Pages)
├── .env                                ← Credenciais Oracle (local, não versionado)
└── .gitignore
```

---

## Contato

Desenvolvido pela equipe de controles contábeis da **SEEC/GDF**.  
Dúvidas sobre os painéis ou a metodologia contábil: entre em contato com a Coordenação de Contabilidade.
