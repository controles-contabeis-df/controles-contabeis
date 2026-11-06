# Atualização automática dos painéis contábeis CONTDF/GDF
# Executado pelo Agendador de Tarefas diariamente às 11:00

$pasta   = $PSScriptRoot
$python  = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
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

# Publica no GitHub tudo o que mudou (um commit + push). Usada ao fim de cada fase.
function Publicar-Paineis($mensagemCommit) {
    $pushMsg = "[$((Get-Date))] Publicando no GitHub ($mensagemCommit)..."
    Write-Output $pushMsg; Add-Content -Path $log -Value $pushMsg
    git -C $pasta add -u
    git -C $pasta add -- "*.html" "*.py" "*.ps1" "README.md" ".gitignore" ".nojekyll"
    git -C $pasta commit -m "$mensagemCommit -- $(Get-Date -Format 'dd/MM/yyyy HH:mm')"
    $pushOut = git -C $pasta push origin main 2>&1; $pushOut | ForEach-Object { Add-Content -Path $log -Value $_ }
    $pushOk = "[$((Get-Date))] Publicado com sucesso. -> https://controles-contabeis-df.github.io/controles-contabeis/"
    Write-Output $pushOk; Add-Content -Path $log -Value $pushOk
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

$env:NO_GIT_PUSH = ""

# FASE A: publica todos os paineis, exceto o do TransfereGov (que depende da extracao mais lenta)
Publicar-Paineis "auto: atualiza paineis"

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

# FASE B: TransfereGov (a extracao leva cerca de 1h15 e a API oscila). Fica por ultimo para que a
# lentidao dela nunca impeca a atualizacao dos demais paineis.
$env:NO_GIT_PUSH = "1"
Executar-Script "extrair_transferegov_df.py"
Executar-Script "extrair_siggo_transferencia.py"
Executar-Script "gerar_painel_transferegov_df.py"
$env:NO_GIT_PUSH = ""
Publicar-Paineis "auto: atualiza painel TransfereGov"

$rodape = @"
============================================================
TODOS OS PAINEIS ATUALIZADOS — $(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')
============================================================
"@
Write-Output $rodape; Add-Content -Path $log -Value $rodape

# Monitoramento semanal de TEDs com mencao ao GDF (o proprio script so executa se a ultima execucao tiver 6+ dias)
Executar-Script "monitorar_ted_gdf.py"

# Confere se o GitHub Pages concluiu o build do ultimo envio; se falhou, pede nova construcao.
# Se persistir o erro, registra em falhas_AAAAMMDD.txt (o alerta_rotina.py avisa por e-mail).
function Verificar-Pages {
    function Log-Pages($t) { $m = "[$(Get-Date)] Pages: $t"; Write-Output $m; Add-Content -Path $log -Value $m }
    try {
        $cfg = @{}
        Get-Content (Join-Path $pasta ".env") | ForEach-Object {
            if ($_ -match '^\s*(GITHUB_TOKEN|GITHUB_USER|GITHUB_REPO)\s*=\s*(.*)$') { $cfg[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'") }
        }
        if (-not $cfg['GITHUB_TOKEN'] -or -not $cfg['GITHUB_USER'] -or -not $cfg['GITHUB_REPO']) { Log-Pages "verificacao ignorada (GITHUB_* ausente no .env)"; return }
        $h = @{ Authorization = "Bearer $($cfg['GITHUB_TOKEN'])"; Accept = "application/vnd.github+json"; "User-Agent" = "atualizar_paineis" }
        $url = "https://api.github.com/repos/$($cfg['GITHUB_USER'])/$($cfg['GITHUB_REPO'])/pages/builds"
        $sha = (git -C $pasta rev-parse HEAD).Trim()
        for ($tentativa = 1; $tentativa -le 2; $tentativa++) {
            $final = $null
            $limite = (Get-Date).AddMinutes(14)
            while ((Get-Date) -lt $limite) {
                $b = Invoke-RestMethod -Uri "$url/latest" -Headers $h -TimeoutSec 60
                if ($b.commit -eq $sha -and $b.status -in @('built', 'errored')) { $final = $b.status; break }
                Start-Sleep -Seconds 30
            }
            if ($final -eq 'built') { Log-Pages "build concluido com sucesso (commit $($sha.Substring(0,7)))."; return }
            if ($tentativa -eq 1) {
                Log-Pages "build $(if ($final) { $final } else { 'sem resposta em 14 min' }); pedindo nova construcao..."
                Invoke-RestMethod -Method Post -Uri $url -Headers $h -TimeoutSec 60 | Out-Null
            }
        }
        Log-Pages "FALHA: o build do GitHub Pages nao concluiu apos nova tentativa (commit $($sha.Substring(0,7)))."
        Add-Content -Path $arqFalhas -Value "GitHub Pages: build nao concluiu apos nova tentativa (commit $($sha.Substring(0,7))) - o site pode estar desatualizado"
    } catch {
        Log-Pages "nao foi possivel verificar ($($_.Exception.Message))"
    }
}
Verificar-Pages

# Aviso por e-mail se algum script falhou ou se alguma extracao veio suspeita (arquivo anterior mantido)
$avisoMsg = "[$((Get-Date))] Verificando falhas da rotina (aviso por e-mail)..."
Write-Output $avisoMsg; Add-Content -Path $log -Value $avisoMsg
$avisoOut = & $python (Join-Path $pasta "alerta_rotina.py") 2>&1
$avisoOut | ForEach-Object { Add-Content -Path $log -Value $_ }
