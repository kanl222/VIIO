param([string]$Report = 'docs/all-tools-workloads-2026-10-27.jsonl')
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$prefix = 'viio-tools-eval-' + [guid]::NewGuid().ToString('N').Substring(0,8)
$envPath = Join-Path $root ('tmp/' + $prefix + '.env')
$configPath = Join-Path $root ('tmp/' + $prefix + '.json')
$remoteConfig = '/tmp/' + $prefix + '.json'
$manifest = Join-Path $PSScriptRoot 'tools-evaluation.compose.yml'
$reportPath = [IO.Path]::GetFullPath((Join-Path $root $Report))
if (-not $reportPath.StartsWith($root + [IO.Path]::DirectorySeparatorChar)) { throw 'Report must stay in the workspace' }
$password = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
[IO.File]::WriteAllText($envPath, "VIIO_EVAL_PREFIX=$prefix`nVIIO_EVAL_PASSWORD=$password`n")
[IO.File]::WriteAllText($configPath, (@{prefix=$prefix; password=$password} | ConvertTo-Json))
$composeArgs = @('compose','--project-name',$prefix,'--env-file',$envPath,'-f',$manifest)
try {
    & docker @composeArgs up -d --wait --wait-timeout 180 tools-eval-postgres
    if ($LASTEXITCODE -ne 0) { throw 'Temporary fixture services did not become ready' }
    & docker cp $configPath "viio_apiagent:$remoteConfig"
    if ($LASTEXITCODE -ne 0) { throw 'Could not copy fixture configuration' }
    & docker cp (Join-Path $PSScriptRoot 'evaluate_all_tools.py') 'viio_apiagent:/tmp/evaluate_all_tools.py'
    if ($LASTEXITCODE -ne 0) { throw 'Could not copy evaluation script' }
    & docker exec viio_apiagent python /tmp/evaluate_all_tools.py $remoteConfig --seed
    if ($LASTEXITCODE -ne 0) { throw 'Could not seed the temporary database' }
    & docker @composeArgs up -d --wait --wait-timeout 180 tools-eval-mcp
    if ($LASTEXITCODE -ne 0) { throw 'Temporary MCP did not become ready' }
    & docker exec viio_apiagent python /tmp/evaluate_all_tools.py $remoteConfig | Out-File -Encoding utf8 $reportPath
    if ($LASTEXITCODE -ne 0) { throw "Evaluation failed; see $Report" }
} finally {
    & docker exec viio_apiagent python -c "from pathlib import Path; Path('$remoteConfig').unlink(missing_ok=True)"
    & docker @composeArgs down --volumes
    # Exact generated files, no directory enumeration or recursive deletion.
    Remove-Item -LiteralPath $envPath,$configPath -Force -ErrorAction SilentlyContinue
}
