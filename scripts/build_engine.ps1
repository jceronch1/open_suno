# Descarga y compila acestep.cpp en Windows (alternativa a los binarios precompilados).
# Necesita git, CMake y Visual Studio Build Tools con "Desarrollo de escritorio con C++".
# Para GPU: CUDA Toolkit (backend cuda) o Vulkan SDK (backend vulkan).
#   powershell -ExecutionPolicy Bypass -File scripts\build_engine.ps1 [-Backend auto|cuda|vulkan|cpu]
param([ValidateSet("auto", "cuda", "vulkan", "cpu")][string]$Backend = "auto")
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "engine\src\acestep.cpp"

foreach ($tool in "git", "cmake") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Write-Host "[ERROR] Falta $tool."; exit 1 }
}
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = if (Test-Path $vswhere) {
    & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
}
if (-not $vs) { Write-Host "[ERROR] Falta Visual Studio Build Tools con C++ (https://visualstudio.microsoft.com/visual-cpp-build-tools/)."; exit 1 }

if (Test-Path (Join-Path $src ".git")) {
    git -C $src pull --ff-only
    git -C $src submodule update --init --recursive
} else {
    New-Item -ItemType Directory -Force (Split-Path -Parent $src) | Out-Null
    git clone --depth 1 --recurse-submodules https://github.com/ServeurpersoCom/acestep.cpp.git $src
}
if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] No se pudo descargar el código de acestep.cpp."; exit 1 }

if ($Backend -eq "auto") {
    if (Get-Command nvcc -ErrorAction SilentlyContinue) { $Backend = "cuda" }
    elseif ($env:VULKAN_SDK) { $Backend = "vulkan" }
    else { $Backend = "cpu" }
}
$flags = switch ($Backend) { "cuda" { "-DGGML_CUDA=ON" } "vulkan" { "-DGGML_VULKAN=ON" } default { "" } }
Write-Host "[Open Suno] Compilando acestep.cpp con backend: $Backend (Visual Studio en $vs)"

$vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
$build = Join-Path $src "build"
cmd /c "call `"$vcvars`" >nul && cmake -S `"$src`" -B `"$build`" $flags && cmake --build `"$build`" --config Release -j $env:NUMBER_OF_PROCESSORS"
if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $build "Release\ace-server.exe"))) {
    Write-Host "[ERROR] La compilación falló."; exit 1
}
Write-Host "[Open Suno] Motor listo en $build\Release"
