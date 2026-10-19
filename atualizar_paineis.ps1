# Atualização automática dos painéis contábeis CONTDF/GDF
# Executado pelo Agendador de Tarefas diariamente às 11:00

$pasta   = $PSScriptRoot
$python  = "C:\Users\clarissa.barbosa\AppData\Local\Programs\Python\Python313\python.exe"
$logDir  = Join-Path $pasta "logs"
$log     = Join-Path $logDir "atualizacao_$(Get-Date -Format 'yyyyMMdd').log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Falhas do dia (lidas por alerta_rotina.py, que envia o aviso por e-mail)
$arqFalhas = Join-Path $logDir "falhas_$(Get-Date -Format 'yyyyMMdd').txt"
$arqAlertasExtracao = Join-Path $logDir "alertas_extracao_$(Get-Date -Format 'yyyyMMdd').txt"
Remove-Item $arqFalhas, $arqAlertasExtracao -ErrorAction SilentlyContinue

function Executar-Script($nome) {
    $inicio = Get-Date
    $msg = "[$inicio] Iniciando: $nome"
    Write-Output $msg; Add-Content -Path $log -Value $msg
    $saida = & $python (Join-Path $pasta $nome) 2>&1
    $codigo = $LASTEXITCODE
    $saida | ForEach-Object { Add-Content -Path $log -Value $_ }
    # Falha = codigo de saida diferente de zero OU traceback do Python na saida
    $teveTraceback = [bool]($saida | ForEach-Object { "$_" } | Select-String -SimpleMatch 'Traceback (most recent call last)' -Quiet)
    if ($codigo -ne 0 -or $teveTraceback) {
        $ultima = ($saida | ForEach-Object { "$_" } | Where-Object { $_.Trim() } | Select-Object -Last 1)
        Add-Content -Path $arqFalhas -Value "$nome (codigo de saida $codigo) - $ultima"
        $msgF = "[$(Get-Date)] FALHA: $nome (codigo $codigo)"
        Write-Output $msgF; Add-Content -Path $log -Value $msgF
    }
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

# Copia diaria das extracoes (14 dias de retencao) ANTES de extrair de novo
Executar-Script "backup_extracoes.py"

Executar-Script "extrair_contas_transitorias.py"
Executar-Script "extrair_disponibilidades.py"
Executar-Script "extrair_disponibilidades_saldo.py"
Executar-Script "extrair_disponibilidade_destinacao_recurso.py"
Executar-Script "extrair_ddr_lancamento.py"
Executar-Script "extrair_empenhos_liquidar.py"
Executar-Script "extrair_repasses.py"
Executar-Script "extrair_empresas_independentes.py"
Executar-Script "extrair_saldo_invertido.py"
Executar-Script "extrair_superavit_a_devolver.py"
Executar-Script "extrair_rpp_por_ne.py"
Executar-Script "extrair_intra_lancamento.py"
Executar-Script "extrair_conciliacao_bens_imoveis_sisgepat.py"
Executar-Script "extrair_conciliacao_bens_moveis_sisgepat.py"
Executar-Script "extrair_conciliacao_bens_intangiveis_sisgepat.py"
Executar-Script "extrair_correspondencia_ativo_passivo.py"
Executar-Script "extrair_correspondencia_ativo_passivo_saldo.py"
Executar-Script "extrair_transferegov_df.py"
Executar-Script "extrair_siggo_transferencia.py"
Executar-Script "gerar_painel_transferegov_df.py"

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

# Aviso por e-mail se algum script falhou ou se alguma extracao veio suspeita (arquivo anterior mantido)
$avisoMsg = "[$((Get-Date))] Verificando falhas da rotina (aviso por e-mail)..."
Write-Output $avisoMsg; Add-Content -Path $log -Value $avisoMsg
$avisoOut = & $python (Join-Path $pasta "alerta_rotina.py") 2>&1
$avisoOut | ForEach-Object { Add-Content -Path $log -Value $_ }
