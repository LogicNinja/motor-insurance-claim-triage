param(
    [string]$Api = 'http://localhost:8001',
    [string]$Model = 'qwen2.5:7b'
)

$ErrorActionPreference = 'Stop'
$envPath = Join-Path (Get-Location) '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw 'Create .env from .env.example before running the prompt comparison.'
}

$tempDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("claim-triage-prompt-eval-" + [guid]::NewGuid().ToString('N'))
$overridePath = Join-Path $tempDirectory 'docker-compose.prompt-eval.yml'
$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
$previousPythonIoEncoding = $env:PYTHONIOENCODING

try {
    New-Item -ItemType Directory -Path $tempDirectory | Out-Null
    @'
services:
  worker:
    environment:
      LLM_BASE_URL: ${LLM_BASE_URL}
      LLM_PROVIDER: ${LLM_PROVIDER}
      LLM_API_KEY: ${LLM_API_KEY}
      LLM_MODEL: ${LLM_MODEL}
      LLM_INPUT_USD_PER_1M: ${LLM_INPUT_USD_PER_1M}
      LLM_OUTPUT_USD_PER_1M: ${LLM_OUTPUT_USD_PER_1M}
      LLM_TIMEOUT_SECONDS: ${LLM_TIMEOUT_SECONDS}
      LLM_MAX_RETRIES: ${LLM_MAX_RETRIES}
      RUN_TIMEOUT_SECONDS: ${RUN_TIMEOUT_SECONDS}
      PROMPT_VERSION: ${PROMPT_VERSION}
'@ | Set-Content -LiteralPath $overridePath -Encoding utf8

    $env:LLM_BASE_URL = 'http://host.docker.internal:11434/v1'
    $env:LLM_PROVIDER = 'ollama'
    $env:LLM_API_KEY = 'ollama'
    $env:LLM_MODEL = $Model
    $env:LLM_INPUT_USD_PER_1M = '0'
    $env:LLM_OUTPUT_USD_PER_1M = '0'
    $env:LLM_TIMEOUT_SECONDS = '180'
    $env:LLM_MAX_RETRIES = '1'
    $env:RUN_TIMEOUT_SECONDS = '240'
    $env:PYTHONIOENCODING = 'utf-8'

    foreach ($version in @('v1', 'v2')) {
        $env:PROMPT_VERSION = $version
        & docker compose -f docker-compose.yml -f $overridePath up -d --no-deps --force-recreate worker
        if ($LASTEXITCODE -ne 0) { throw "Could not recreate worker for prompt $version." }

        $workerSettings = & docker compose exec -T worker python -c "from shared.config import get_settings; s=get_settings(); print(f'{s.llm_provider}|{s.llm_model}|{s.prompt_version}')"
        if ($LASTEXITCODE -ne 0 -or $workerSettings.Trim() -ne "ollama|$Model|$version") {
            throw "Worker config mismatch for prompt ${version}: $workerSettings"
        }

        $output = Join-Path (Get-Location) "eval/prompt_$($version)_results.json"
        & $python eval/run_eval.py --api $Api --file eval/scenarios.yaml --timeout 360 --output $output --resume
        if ($LASTEXITCODE -ne 0) { throw "Evaluation failed for prompt $version; inspect the output above." }
    }

    & $python eval/compare_prompts.py `
        (Join-Path (Get-Location) 'eval/prompt_v1_results.json') `
        (Join-Path (Get-Location) 'eval/prompt_v2_results.json') `
        --output eval/prompt_comparison.json
    if ($LASTEXITCODE -ne 0) { throw 'Prompt comparison did not complete.' }
} finally {
    Remove-Item Env:LLM_BASE_URL,Env:LLM_PROVIDER,Env:LLM_API_KEY,Env:LLM_MODEL,Env:LLM_INPUT_USD_PER_1M,Env:LLM_OUTPUT_USD_PER_1M,Env:LLM_TIMEOUT_SECONDS,Env:LLM_MAX_RETRIES,Env:RUN_TIMEOUT_SECONDS,Env:PROMPT_VERSION -ErrorAction SilentlyContinue
    if ($null -eq $previousPythonIoEncoding) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $previousPythonIoEncoding }
    & docker compose up -d --no-deps --force-recreate worker
    if ($LASTEXITCODE -ne 0) { Write-Warning 'The worker did not restart with the original .env; run docker compose up -d --force-recreate worker.' }
    if (Test-Path -LiteralPath $tempDirectory) { Remove-Item -LiteralPath $tempDirectory -Recurse -Force }
}
