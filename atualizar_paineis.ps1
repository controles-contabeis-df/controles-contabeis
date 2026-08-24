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
$gitMsg = "[$((Get-Date))] git pull --rebase --autostash"
Write-Output $gitMsg; Add-Content -Path $log -Value $gitMsg
$gitOut = git -C $pasta pull origin main --rebase --autostash 2>&1; $gitOut | ForEach-Object { Add-Content -Path $log -Value $_ }

# Bloqueia pushes individuais dos scripts — um único push consolidado ao final
$env:NO_GIT_PUSH = "1"

Executar-Script "extrair_contas_transitorias.py"
Executar-Script "extrair_disponibilidades.py"
Executar-Script "extrair_disponibilidades_saldo.py"
Executar-Script "extrair_disponibilidade_destinacao_recurso.py"
Executar-Script "extrair_ddr_lancamento.py"
Executar-Script "extrair_empenhos_liquidar.py"
Executar-Script "extrair_repasses.py"
Executar-Script "extrair_empresas_independentes.py"
Executar-Script "extrair_saldo_invertido.py"
Executar-Script "extrair_rpp_por_ne.py"
Executar-Script "extrair_intra_lancamento.py"
Executar-Script "extrair_conciliacao_bens_imoveis_sisgepat.py"
Executar-Script "extrair_conciliacao_bens_moveis_sisgepat.py"
Executar-Script "extrair_conciliacao_bens_intangiveis_sisgepat.py"
Executar-Script "extrair_correspondencia_ativo_passivo.py"
Executar-Script "extrair_correspondencia_ativo_passivo_saldo.py"

$env:NO_GIT_PUSH = ""

# Push consolidado — dispara apenas UM build do GitHub Pages por dia
$pushMsg = "[$((Get-Date))] Publicando todos os paineis no GitHub (push consolidado)..."
Write-Output $pushMsg; Add-Content -Path $log -Value $pushMsg
git -C $pasta add -A
$commitMsg = "auto: atualiza paineis -- $(Get-Date -Format 'dd/MM/yyyy HH:mm')"
git -C $pasta commit -m $commitMsg
$pushOut = git -C $pasta push origin main 2>&1; $pushOut | ForEach-Object { Add-Content -Path $log -Value $_ }
$pushOk = "[$((Get-Date))] Publicado com sucesso. -> https://controles-contabeis-df.github.io/controles-contabeis/"
Write-Output $pushOk; Add-Content -Path $log -Value $pushOk

$rodape = @"
============================================================
TODOS OS PAINEIS ATUALIZADOS — $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')
============================================================
"@
Write-Output $rodape; Add-Content -Path $log -Value $rodape

# Verificação de equações intragovernamentais e envio de alerta por e-mail
$alertaMsg = "[$((Get-Date))] Verificando equações intragovernamentais (alerta por e-mail)..."
Write-Output $alertaMsg; Add-Content -Path $log -Value $alertaMsg
$alertaOut = & $python (Join-Path $pasta "alerta_lancamentos_intra.py") 2>&1
$alertaOut | ForEach-Object { Add-Content -Path $log -Value $_ }

$alerta2Msg = "[$((Get-Date))] Verificando transferências intragovernamentais 351120 x 451120..."
Write-Output $alerta2Msg; Add-Content -Path $log -Value $alerta2Msg
$alerta2Out = & $python (Join-Path $pasta "alerta_transferencias_intra.py") 2>&1
$alerta2Out | ForEach-Object { Add-Content -Path $log -Value $_ }

$alertaFim = "[$((Get-Date))] Verificação de equações concluída."
Write-Output $alertaFim; Add-Content -Path $log -Value $alertaFim
