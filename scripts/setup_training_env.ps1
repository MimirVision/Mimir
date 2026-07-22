param(
    [string]$EnvironmentPath = ".venv-training"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$environment = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $EnvironmentPath))
$python = Join-Path $environment "Scripts\python.exe"

function Assert-LastCommand([string]$description) {
    if ($LASTEXITCODE -ne 0) {
        throw "$description failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    python -m venv $environment
}

& $python -m pip install --upgrade pip
Assert-LastCommand "pip upgrade"
& $python -m pip install `
    torch==2.11.0 `
    torchvision==0.26.0 `
    --index-url https://download.pytorch.org/whl/cu128
Assert-LastCommand "CUDA PyTorch installation"
& $python -m pip install -r (Join-Path $repoRoot "requirements-training.txt")
Assert-LastCommand "training dependency installation"

& $python -c @"
import importlib.util
import torch
import onnxruntime as ort

assert torch.cuda.is_available(), "CUDA PyTorch could not access the NVIDIA GPU"
assert importlib.util.find_spec("onnx") is not None, "ONNX is unavailable"
assert "CUDAExecutionProvider" in ort.get_available_providers(), "ONNX Runtime CUDA provider is unavailable"
print(f"PyTorch {torch.__version__}")
print(f"CUDA runtime {torch.version.cuda}")
print(f"Training device {torch.cuda.get_device_name(0)}")
print(f"ONNX Runtime {ort.__version__}")
"@
Assert-LastCommand "training environment verification"
