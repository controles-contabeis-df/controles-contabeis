# Controles Contábeis — GDF

Dashboards interativos de controles contábeis do Governo do Distrito Federal, publicados via GitHub Pages e alimentados por ETL automatizado a partir do banco Oracle (SIGGO).

🔗 **Acesso público:** https://controles-contabeis-df.github.io/controles-contabeis/

---

## Arquitetura

```
Oracle (ORAPRD06 — 10.69.1.118:1521)
    ↓ extrair_disponibilidades.py      (Python + oracledb)  → disponibilidades_lancamento.html
    ↓ extrair_contas_transitorias.py   (Python + oracledb)  → contas_transitorias.html
    ↓ extrair_[painel].py              (Python + oracledb)  → [painel].html  (a criar)
         ↓
    atualizar_dados.bat   →  executa todos os scripts em sequência
         ↓
    git push → GitHub Pages
         ↓
    index.html  (portal de navegação)
    ├── disponibilidades_lancamento.html
    ├── contas_transitorias.html
    └── [próximos painéis]
```

O ETL roda automaticamente todo **dia útil escolhido do mês** via **Agendador de Tarefas do Windows** na estação local.

---

## Dashboards disponíveis

| Dashboard | Arquivo HTML | Script ETL | Descrição |
|-----------|-------------|------------|-----------|
| Página inicial | `index.html` | — | Portal de navegação — Controles de Encerramento do Exercício |
| Disponibilidades por Lançamento | `disponibilidades_lancamento.html` | `extrair_disponibilidades.py` | Lançamentos que originam diferenças entre AF, PF, RPNP e conta 721190300 |
| Contas Transitórias | `contas_transitorias.html` | `extrair_contas_transitorias.py` | Saldos das 15 contas transitórias que devem zerar ao fim do exercício |

---

## Estrutura do projeto

```
controles-contabeis/                         # Repositório GitHub Pages
├── index.html                               # Portal de navegação
├── disponibilidades_lancamento.html         # Dashboard — Disponibilidades por Lançamento
├── contas_transitorias.html                 # Dashboard — Contas Transitórias
│
└── C:\Painéis Qlik Sense\                   # Pasta local na estação de trabalho
    ├── extrair_disponibilidades.py          # ETL — Disponibilidades por Lançamento
    ├── extrair_contas_transitorias.py       # ETL — Contas Transitórias
    └── atualizar_dados.bat                  # Executa todos os ETLs em sequência
```

---

## Fonte dos dados

| Dado | Fonte | View Oracle |
|------|-------|-------------|
| Contas contábeis permitidas | SIGGO | `MIL2026.VCONTACONTABIL` |
| Lançamentos contábeis | SIGGO | `MIL2026.VLANCAMENTOCONTABIL` |

> **Nota:** o schema `MIL2026` corresponde ao exercício vigente. Para exercícios futuros, ajuste a variável `SCHEMA` no topo de cada script de extração.

---

## Lógica dos controles

### Disponibilidades por Lançamento

Identifica lançamentos contábeis por Unidade Gestora que originam diferenças segundo a equação:

```
Diferença (a − b) = AF − (PF + RPNP + Conta 721190300)
```

| Coluna | Descrição | Contas | Sinal |
|--------|-----------|--------|-------|
| AF | Ativo Financeiro | 100000000 – 199999999 | D = + / C = − |
| PF | Passivo Financeiro | 200000000 – 229999999 | C = + / D = − |
| RPNP | Restos a Pagar Não Processados | 631100000 | C = + / D = − |
| Conta 721190300 | Conta específica | 721190300 | D = + / C = − |

Somente contas com `INSISCONTABIL IN ('F', 'C', 'O')` são consideradas.

---

## Configuração do ambiente

### Pré-requisitos

- Python 3.10+
- Acesso à rede interna do GDF (IP `10.69.1.118`)
- Token GitHub com permissão `repo`

### Instalar dependências

```cmd
pip install oracledb pandas
```

### Credenciais Oracle e GitHub

As credenciais estão fixas nos scripts. Para alterar, edite as variáveis no topo de cada arquivo:

```python
# em extrair_disponibilidades.py (e demais scripts de extração)
ORACLE_USER  = "usefp79"
ORACLE_PASS  = "bo39ra"
ORACLE_DSN   = "10.69.1.118:1521/oraprd06"
GITHUB_TOKEN = "seu_token_aqui"   # ← nunca versionar o token real
GITHUB_USER  = "controles-contabeis-df"
GITHUB_REPO  = "controles-contabeis"
```

### Gerar novo token GitHub

1. Acesse: https://github.com/settings/tokens
2. Clique em **Generate new token (classic)**
3. Note: `controles-contabeis`  ·  Expiration: `No expiration`
4. Marque: ✅ **repo**
5. Copie o token e substitua em `GITHUB_TOKEN` nos scripts

---

## Executar os scripts manualmente

```cmd
cd "C:\Painéis Qlik Sense"
python extrair_disponibilidades.py
```

O script gera o arquivo `disponibilidades_lancamento.html` na mesma pasta com os dados já embutidos, e faz push automático para o GitHub Pages.

Para filtrar por Unidade Gestora específica:

```cmd
python extrair_disponibilidades.py --ug 10101
```

---

## Lógica do ETL

### extrair_disponibilidades.py

```
1. Conecta ao Oracle via oracledb (thin mode — sem client instalado)
2. Executa SQL com CTEs:
   contas_permitidas  → filtra VCONTACONTABIL por INSISCONTABIL IN ('F','C','O')
   lancamentos_base   → agrega VLANCAMENTOCONTABIL com INNER JOIN nas contas permitidas
   SELECT final       → calcula AF, PF, RPNP, Conta 721190300, AF−(PF+RPNP), Diferença (a−b)
3. Converte resultado para lista de registros JSON
4. Gera arquivo HTML autocontido com os dados embutidos
5. Faz push do HTML para o repositório GitHub (GitHub Pages)
```

---

## Automação — Agendador de Tarefas do Windows

O script `atualizar_dados.bat` rodará automaticamente na periodicidade definida via **Agendador de Tarefas do Windows**.

### Criar a tarefa

1. `Win + R` → `taskschd.msc`
2. **Criar Tarefa Básica**
3. Preencher:
   - **Nome:** `Atualização Controles Contábeis`
   - **Gatilho:** Mensalmente, no dia e horário desejados
   - **Ação:** `C:\Painéis Qlik Sense\atualizar_dados.bat`
   - **Pasta de trabalho:** `C:\Painéis Qlik Sense`

### Verificar se a tarefa existe

```
Win + R → taskschd.msc → Biblioteca do Agendador → "Atualização Controles Contábeis"
```

### Executar manualmente

Clique com botão direito na tarefa → **Executar**

Ou via linha de comando:
```cmd
"C:\Painéis Qlik Sense\atualizar_dados.bat"
```

---

---

## Lógica dos controles

### Contas Transitórias

Monitora o saldo das contas transitórias ao longo do exercício. Essas contas **não podem encerrar o ano com saldo diferente de zero** — qualquer valor remanescente indica pendência a regularizar antes do encerramento.

| Conta Contábil | Grupo |
|---------------|-------|
| 113810604, 113810699 | Ativo — grupo 1138 |
| 113819101, 113819103 | Ativo — grupo 1138 |
| 113829101, 113829102, 113829103 | Ativo — grupo 1138 |
| 218815001 a 218815005, 218815008 a 218815010 | Passivo — grupo 2188 |

**Fórmula:** `SALDO = SUM(VACREDITO − VADEBITO)` por UG, mês e conta.

**Filtros disponíveis:** Exercício, Mês, Unidade Gestora, Tipo de Agregação, Conta Contábil e botão **"Saldo ≠ 0"** para visualizar apenas pendências.

**Fonte de dados:** `MIL2026.VSALDOCONTABIL` + JOINs com `UNIDADEGESTORA`, `GESTAO`, `TIPOAGREGACAOADM` e `TIPOAGREGACAO`.

---

## Adicionar novo dashboard

1. Crie o script de extração seguindo o padrão de `extrair_disponibilidades.py`
2. O script deve gerar um arquivo HTML autocontido com os dados embutidos
3. Adicione a chamada do novo script em `atualizar_dados.bat`
4. Adicione um novo card em `index.html` apontando para o novo arquivo
5. Documente o novo painel na tabela **Dashboards disponíveis** deste README

---

## Reverter dados para versão anterior

### Ver histórico de commits

```cmd
git log --oneline
```

### Restaurar um arquivo específico

```cmd
git checkout <hash> -- disponibilidades_lancamento.html
git add .
git commit -m "fix: reverte disponibilidades para versão anterior"
git push origin main
```

### Desfazer o último commit

```cmd
git revert HEAD
git push origin main
```

---

## Navegação dos dashboards

```
index.html  (Portal — Controles de Encerramento do Exercício)
├── disponibilidades_lancamento.html   → Disponibilidades por Lançamento
└── contas_transitorias.html           → Contas Transitórias
```

---

## Contato e responsável

**Responsável:** Clarissa Barbosa
**Setor:** Secretaria de Estado de Economia — Distrito Federal
**Repositório:** https://github.com/controles-contabeis-df/controles-contabeis
**Dashboard:** https://controles-contabeis-df.github.io/controles-contabeis/
