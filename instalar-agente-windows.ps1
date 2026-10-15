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

# Stop instead of copying code over a running Python process. Otherwise the
# previous in-memory app remains active and continues creating accounts.
function Test-AgentPortBusy {
    $Client = New-Object System.Net.Sockets.TcpClient
    try {
        $Async = $Client.BeginConnect("127.0.0.1", 8767, $null, $null)
        if (-not $Async.AsyncWaitHandle.WaitOne(700)) { return $false }
        try {
            $Client.EndConnect($Async)
            return $true
        } catch {
            return $false
        }
    } catch {
        return $false
    } finally {
        $Client.Close()
    }
}
if (Test-AgentPortBusy) {
    throw "Agente anterior na porta 8767. Feche a janela do agente Windows antes de atualizar. Seus perfis serao preservados."
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
    if ($Branch -eq "fix/remover-criacao-contas") {
        $Maefile = Join-Path $Source "Settings\Criar_Mae\gui_tab.py"
        $Filhafile = Join-Path $Source "Settings\Criar_Filha\gui_tab.py"
        $Serverfile = Join-Path $Source "servidor.py"
        foreach ($LaunchFile in @($Maefile, $Filhafile)) {
            if (-not (Test-Path -LiteralPath $LaunchFile) -or
                -not (Select-String -LiteralPath $LaunchFile -SimpleMatch "Abrir Navegadores" -Quiet) -or
                (Select-String -LiteralPath $LaunchFile -SimpleMatch "create_launch_profiles" -Quiet)) {
                throw "ZIP incorreto: esta versao deve abrir navegadores SEM gerar contas."
            }
        }
        if (-not (Select-String -LiteralPath $Serverfile -SimpleMatch "NEXORA_EDITION = 'browser-only'" -Quiet)) {
            throw "ZIP incorreto: falta identificador da versao sem cadastro."
        }
        if (Test-Path -LiteralPath (Join-Path $Source "nexora_accounts\registration_autofill.py")) {
            throw "ZIP incorreto: ainda contem cadastro antigo."
        }
    }

    New-Item -ItemType Directory -Path $AgentDir -Force | Out-Null
    # Never touch user data: __profiles/, conta(s)/, accounts.json, .venv/,
    # logs/ and saved proxy configuration remain untouched on disk.
    # Old installers only overlaid source files; they never removed modules
    # that disappeared from GitHub. Remove only audited obsolete CODE paths.
    if ($Branch -eq "fix/remover-criacao-contas") {
        $ObsoleteSourcePaths = @(
            "nexora_accounts\profile_data.py",
            "nexora_accounts\registration_autofill.py",
            "nexora_accounts\registration_submit.py",
            "nexora_accounts\registration_trace.py",
            "nexora_accounts\service.py",
            "Settings\Criar_Mae\settings\actions.py",
            "Settings\Criar_Mae\settings\identity.py",
            "Settings\Criar_Mae\settings\nomes.txt",
            "Settings\Criar_Filha\settings\actions.py",
            "Settings\Criar_Filha\settings\identity.py",
            "Settings\Criar_Filha\settings\nomes.txt",
            "webui\static\accounts_view.js",
            "webui\static\accounts_view.css",
            "proxybet_registration_bridge",
            "docs\REGISTRATION_PREFILL.md",
            "docs\DIAGNOSTICO_CONTAS.md"
        )
        foreach ($Relative in $ObsoleteSourcePaths) {
            $OldCode = Join-Path $AgentDir $Relative
            if (Test-Path -LiteralPath $OldCode) {
                Remove-Item -LiteralPath $OldCode -Recurse -Force -ErrorAction Stop
                Write-Host "Modulo antigo removido: $Relative"
            }
        }
        # Prevent any stale compiled registration modules surviving as direct
        # sourceless imports in the package directories.
        $OldBytecodeNames = @(
            "profile_data", "registration_autofill", "registration_submit",
            "registration_trace", "service", "actions", "identity"
        )
        foreach ($RelCache in @(
            "nexora_accounts\__pycache__",
            "Settings\Criar_Mae\settings\__pycache__",
            "Settings\Criar_Filha\settings\__pycache__"
        )) {
            $Cache = Join-Path $AgentDir $RelCache
            if (Test-Path -LiteralPath $Cache) {
                foreach ($Stem in $OldBytecodeNames) {
                    Get-ChildItem -LiteralPath $Cache -File -Filter "$Stem.*.pyc" |
                        Remove-Item -Force -ErrorAction Stop
                }
            }
        }
    }
    Copy-Item -Path (Join-Path $Source "*") -Destination $AgentDir -Recurse -Force
    if ($Branch -eq "fix/remover-criacao-contas") {
        $InstalledMaefile = Join-Path $AgentDir "Settings\Criar_Mae\gui_tab.py"
        if (-not (Select-String -LiteralPath $InstalledMaefile -SimpleMatch "Abrir Navegadores" -Quiet) -or
            (Select-String -LiteralPath $InstalledMaefile -SimpleMatch "create_launch_profiles" -Quiet)) {
            throw "Falha ao atualizar: o Windows ainda contem a rotina antiga. Agente NAO iniciado."
        }
        Write-Host "OK: codigo sem cadastro instalado; pastas __profiles preservadas." -ForegroundColor Green
    }
    [IO.File]::WriteAllText(
        (Join-Path $AgentDir "versao-instalada.txt"),
        "Branch: $Branch" + [Environment]::NewLine + "Atualizado em: $(Get-Date -Format s)",
        [Text.Encoding]::UTF8
    )
    # One permanent local launcher; no need to download a new CMD ever again.
    $ResumeFile = Join-Path $AgentDir "iniciar-agente-local.cmd"
    if ($Branch -eq "fix/remover-criacao-contas") {
        if (-not (Test-Path -LiteralPath (Join-Path $AgentDir "atualizador_nexora.py"))) {
            throw "Atualizador automatico nao encontrado no pacote."
        }
        $LocalConfig = @{ panel = $Painel; branch = $Branch } | ConvertTo-Json
        [IO.File]::WriteAllText(
            (Join-Path $AgentDir "nexora-updater-config.json"),
            $LocalConfig, [Text.Encoding]::UTF8
        )
        # Avoid installing the same commit twice after initial setup.
        try {
            $Ref = Invoke-RestMethod -Uri (
                "https://api.github.com/repos/gustavo123123131/nexora3/git/ref/heads/$Branch"
            ) -TimeoutSec 15 -Headers @{ "User-Agent" = "Nexora-Windows-Installer" }
            if ($Ref.object.sha -match '^[a-f0-9]{40}$') {
                $Version = @{ branch = $Branch; sha = $Ref.object.sha } | ConvertTo-Json
                [IO.File]::WriteAllText(
                    (Join-Path $AgentDir "nexora-update-state.json"),
                    $Version, [Text.Encoding]::UTF8
                )
            }
        } catch {
            Write-Host "A versao sera conferida pelo atualizador na primeira verificacao."
        }
        $resume = @(
            '@echo off',
            'title Nexora3 - Atualizador automatico Windows',
            'cd /d "%~dp0"',
            '".venv\Scripts\python.exe" atualizador_nexora.py --branch fix/remover-criacao-contas',
            'pause'
        ) -join [Environment]::NewLine
    } else {
        $resume = @(
            '@echo off',
            'cd /d "%~dp0"',
            '".venv\Scripts\python.exe" agente_windows.py',
            'pause'
        ) -join [Environment]::NewLine
    }
    [IO.File]::WriteAllText($ResumeFile, $resume, [Text.Encoding]::ASCII)
    Write-Host "Iniciador fixo: $ResumeFile (nao precisa baixar novamente)"

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
        if ($Branch -eq "fix/remover-criacao-contas") {
            Write-Host "NEXORA: iniciando supervisor automatico. Mantenha esta janela aberta." -ForegroundColor Green
            Write-Host "O agente se atualizara e reiniciara quando suas janelas estiverem fechadas."
            & $VenvPython atualizador_nexora.py --branch $Branch --painel $Painel
        } else {
            Write-Host "Iniciando agente." -ForegroundColor Green
            & $VenvPython agente_windows.py --painel $Painel
        }
        if ($LASTEXITCODE -ne 0) { throw "O agente encerrou com erro." }
    } finally {
        Pop-Location
    }
} finally {
    if (Test-Path $Scratch) {
        Remove-Item -Path $Scratch -Force -Recurse -ErrorAction SilentlyContinue
    }
}
