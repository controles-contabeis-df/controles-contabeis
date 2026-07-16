# Atualização automática dos painéis contábeis CONTDF/GDF
# Executado pelo Agendador de Tarefas diariamente às 11:00

$pasta   = $PSScriptRoot
$python  = "C:\Users\clarissa.barbosa\AppData\Local\Programs\Python\Python313\python.exe"
$logDir  = Join-Path $pasta "logs"
$log     = Join-Path $logDir "atualizacao_$(Get-Date -Format 'yyyyMMdd').log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Executar-Script($nome) {
    $inicio = Get-Date
    $msg = "[$inicio] Iniciando: $nome"
    Write-Output $msg; Add-Content -Path $log -Value $msg
    $saida = & $python (Join-Path $pasta $nome) 2>&1
    $saida | ForEach-Object { Add-Content -Path $log -Value $_ }
    $fim = Get-Date
    $duracao = ($fim - $inicio).ToString("mm\:ss")
    $msg2 = "[$fim] Concluido: $nome (duracao: $duracao)"
    Write-Output $msg2; Add-Content -Path $log -Value $msg2
    Add-Content -Path $log -Value ""
}

$cabecalho = @"
============================================================
ATUALIZACAO DOS PAINEIS — $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')
============================================================
"@
Write-Output $cabecalho; Add-Content -Path $log -Value $cabecalho

# Garante que o diretório de trabalho seja a pasta dos scripts
Set-Location $pasta

# Sincronizar git antes de publicar (evita push rejeitado por divergência)
$gitMsg = "[$((Get-Date))] git stash + pull --rebase + stash pop"
Write-Output $gitMsg; Add-Content -Path $log -Value $gitMsg
$gitOut = git -C $pasta stash 2>&1; $gitOut | ForEach-Object { Add-Content -Path $log -Value $_ }
$gitOut = git -C $pasta pull origin main --rebase 2>&1; $gitOut | ForEach-Object { Add-Content -Path $log -Value $_ }
$gitOut = git -C $pasta stash pop 2>&1; $gitOut | ForEach-Object { Add-Content -Path $log -Value $_ }

Executar-Script "extrair_disponibilidades.py"
Executar-Script "extrair_disponibilidades_saldo.py"
Executar-Script "extrair_disponibilidade_destinacao_recurso.py"
Executar-Script "extrair_ddr_lancamento.py"
Executar-Script "extrair_empenhos_liquidar.py"
Executar-Script "extrair_repasses.py"
Executar-Script "extrair_empresas_independentes.py"

$rodape = @"
============================================================
TODOS OS PAINEIS ATUALIZADOS — $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')
============================================================
"@
Write-Output $rodape; Add-Content -Path $log -Value $rodape
