@echo off
cd /d "C:\Users\clarissa.barbosa\Documents\Painéis Qlik Sense"

echo [%date% %time%] ============================================ >> logs\atualizacao.log
echo [%date% %time%] Iniciando atualização dos painéis... >> logs\atualizacao.log
echo [%date% %time%] ============================================ >> logs\atualizacao.log

echo [%date% %time%] [1/5] Contas Transitórias... >> logs\atualizacao.log
python extrair_contas_transitorias.py >> logs\atualizacao.log 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] ERRO no painel Contas Transitórias. >> logs\atualizacao.log
) else (
    echo [%date% %time%] OK - Contas Transitórias concluído. >> logs\atualizacao.log
)

echo [%date% %time%] [2/5] Empenhos a Liquidar... >> logs\atualizacao.log
python extrair_empenhos_liquidar.py >> logs\atualizacao.log 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] ERRO no painel Empenhos a Liquidar. >> logs\atualizacao.log
) else (
    echo [%date% %time%] OK - Empenhos a Liquidar concluído. >> logs\atualizacao.log
)

echo [%date% %time%] [3/5] Disponibilidades por Lançamento... >> logs\atualizacao.log
python extrair_disponibilidades.py >> logs\atualizacao.log 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] ERRO no painel Disponibilidades. >> logs\atualizacao.log
) else (
    echo [%date% %time%] OK - Disponibilidades concluído. >> logs\atualizacao.log
)

echo [%date% %time%] [4/5] Disponibilidades por Saldo... >> logs\atualizacao.log
python extrair_disponibilidades_saldo.py >> logs\atualizacao.log 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] ERRO no painel Disponibilidades por Saldo. >> logs\atualizacao.log
) else (
    echo [%date% %time%] OK - Disponibilidades por Saldo concluído. >> logs\atualizacao.log
)

echo [%date% %time%] [5/5] Disponibilidade por Destinação de Recurso por Saldo... >> logs\atualizacao.log
python extrair_disponibilidade_destinacao_recurso.py >> logs\atualizacao.log 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] ERRO no painel Disponibilidade por Destinação de Recurso. >> logs\atualizacao.log
) else (
    echo [%date% %time%] OK - Disponibilidade por Destinação de Recurso concluído. >> logs\atualizacao.log
)

echo [%date% %time%] [6/6] DDR por Lançamento... >> logs\atualizacao.log
python extrair_ddr_lancamento.py >> logs\atualizacao.log 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] ERRO no painel DDR por Lançamento. >> logs\atualizacao.log
) else (
    echo [%date% %time%] OK - DDR por Lançamento concluído. >> logs\atualizacao.log
)

echo [%date% %time%] ============================================ >> logs\atualizacao.log
echo [%date% %time%] Atualização finalizada. >> logs\atualizacao.log
echo [%date% %time%] ============================================ >> logs\atualizacao.log
