# Lixo CPA - instalador do agente local de Windows.
# Baixa somente o codigo publico do GitHub e instala Chromium no PC.
param([string]$Painel = "", [string]$Branch = "main")
$ErrorActionPreference = "Stop"
if ($Branch -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,120}$' -or
    $Branch -match '(^|/)\.\.?($|/)' -or $Branch -match '//') {
    throw "Branch invalida."
}
Write-Host "Origem do codigo: $Branch"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host ""
Write-Host "=== Lixo CPA: Chromium no Windows ===" -ForegroundColor Cyan
Write-Host "O painel fica no Codespaces; Chromium e perfis ficam neste PC."
Write-Host "Instalacao local necessaria (Python e Chromium)."
Write-Host ""
if (-not $Painel) {
    $Painel = Read-Host "Cole o link HTTPS da porta 8765 (Codespaces)"
}
if ($Painel -notmatch '^https://[a-zA-Z0-9-]+-8765(\.preview)?\.app\.github\.dev/?$') {
    throw "Link invalido. Copie o link HTTPS da porta 8765 do GitHub Codespaces."
}
$Painel = $Painel.TrimEnd('/')

$PythonLauncher = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonLauncher = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonLauncher = "python"
} else {
    Write-Host "Python nao foi encontrado neste Windows." -ForegroundColor Red
    Write-Host "Instale Python 3.11 ou mais recente de https://www.python.org/downloads/windows/"
    Write-Host "Marque 'Add Python to PATH'. Depois execute este instalador novamente."
    exit 1
}

$AgentDir = Join-Path $env:LOCALAPPDATA "LixoCPA-Agent"
$Scratch = Join-Path $env:TEMP ("lixocpa-agent-" + [Guid]::NewGuid().ToString("N"))
$Archive = Join-Path $Scratch "main.zip"
$Expanded = Join-Path $Scratch "repo"
New-Item -ItemType Directory -Path $Scratch -Force | Out-Null
try {
    Write-Host "Baixando o codigo do seu repositorio GitHub..."
    Invoke-WebRequest -Uri "https://github.com/gustavo123123131/nexora3/archive/refs/heads/$Branch.zip" -OutFile $Archive -UseBasicParsing
    Expand-Archive -LiteralPath $Archive -DestinationPath $Expanded -Force
    $Sources = @(Get-ChildItem -LiteralPath $Expanded -Directory)
    if ($Sources.Count -ne 1) { throw "Arquivo GitHub invalido: diretorio do projeto nao localizado." }
    $Source = $Sources[0].FullName
    if (-not (Test-Path (Join-Path $Source "agente_windows.py"))) {
        throw "O ZIP do repositorio nao contem o agente Windows."
    }

    New-Item -ItemType Directory -Path $AgentDir -Force | Out-Null
    # Nao apaga perfis nem configuracoes locais de execucoes anteriores.
    Copy-Item -Path (Join-Path $Source "*") -Destination $AgentDir -Recurse -Force
    # Na proxima vez, basta executar o CMD abaixo: sem download ou pip.
    $ResumeFile = Join-Path $AgentDir "iniciar-agente-local.cmd"
    $resume = @(
        '@echo off',
        'cd /d "%~dp0"',
        '".venv\Scripts\python.exe" agente_windows.py',
        'pause'
    ) -join [Environment]::NewLine
    [IO.File]::WriteAllText($ResumeFile, $resume, [Text.Encoding]::ASCII)
    Write-Host "Proxima execucao: $ResumeFile"
    $VenvDir = Join-Path $AgentDir ".venv"
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        Write-Host "Criando o ambiente virtual de Python..."
        if ($PythonLauncher -eq "py") {
            & py -3 -m venv $VenvDir
        } else {
            & python -m venv $VenvDir
        }
        if ($LASTEXITCODE -ne 0) { throw "Falha ao criar ambiente virtual Python." }
    }
    # Mostrar somente etapas e eventuais erros. Detalhes ficam em arquivo local.
    $LogDir = Join-Path $AgentDir "logs"
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $InstallLog = Join-Path $LogDir "instalacao.log"

    function Invoke-QuietInstall {
        param([string]$Label, [string[]]$CommandArgs)

        Write-Host $Label
        $StandardOut = Join-Path $LogDir "instalacao.stdout.tmp"
        $StandardErr = Join-Path $LogDir "instalacao.stderr.tmp"
        Add-Content -LiteralPath $InstallLog -Value ("--- " + (Get-Date -Format s) + ": " + $Label)
        $Process = Start-Process -FilePath $VenvPython -ArgumentList $CommandArgs `
            -WorkingDirectory $AgentDir -Wait -PassThru -NoNewWindow `
            -RedirectStandardOutput $StandardOut -RedirectStandardError $StandardErr
        foreach ($TempLog in @($StandardOut, $StandardErr)) {
            if (Test-Path -LiteralPath $TempLog) {
                Get-Content -LiteralPath $TempLog | Add-Content -LiteralPath $InstallLog
                Remove-Item -LiteralPath $TempLog -Force
            }
        }
        if ($Process.ExitCode -ne 0) {
            Write-Host "ERRO ao concluir: $Label" -ForegroundColor Red
            Write-Host "Ultimas mensagens do diagnostico:" -ForegroundColor Yellow
            Get-Content -LiteralPath $InstallLog -Tail 12
            throw "Consulte o log completo: $InstallLog"
        }
        Write-Host "OK" -ForegroundColor Green
    }

    Push-Location $AgentDir
    try {
        Invoke-QuietInstall -Label "Verificando dependencias Python..." `
            -CommandArgs @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", "requirements-web.txt")
        Invoke-QuietInstall -Label "Verificando Chromium..." `
            -CommandArgs @("-m", "playwright", "install", "chromium")
        Write-Host ""
        Write-Host "Iniciando agente. Nao feche esta janela enquanto usar o Lixo CPA." -ForegroundColor Green
        & $VenvPython agente_windows.py --painel $Painel
        if ($LASTEXITCODE -ne 0) { throw "O agente encerrou com erro." }
    } finally {
        Pop-Location
    }
} finally {
    if (Test-Path $Scratch) {
        Remove-Item -Path $Scratch -Force -Recurse -ErrorAction SilentlyContinue
    }
}
