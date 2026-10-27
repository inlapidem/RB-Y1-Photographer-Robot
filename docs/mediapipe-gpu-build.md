# MediaPipe GPU wheel (Ubuntu and Jetson)

The PyPI Linux wheel may expose `BaseOptions.Delegate.GPU` while being built
without the GPU calculators. Geekseek therefore uses a locally built wheel.
Build once per CPU architecture; an x86_64 wheel cannot be installed on a
Jetson (aarch64), or vice versa.

MediaPipe's Linux GPU delegate uses EGL/OpenGL ES. A CUDA toolkit alone is not
enough: a working NVIDIA display/compute driver and EGL/GLES development
headers must also be present.

## Ubuntu x86_64 (conda)

Geekseek 개발에 이미 사용하는 `3dgs` 환경에서 빌드합니다. PyTorch는
설치하거나 변경하지 않습니다. GCC/G++ 13이 없다면 먼저 컴파일러 패키지만
추가합니다.

```bash
conda install -y -n 3dgs -c conda-forge \
  gcc_linux-64=13 gxx_linux-64=13
conda run -n 3dgs bash -c '\
  PYTHON_BIN="$CONDA_PREFIX/bin/python" \
  BUILD_ROOT="$HOME/build/mediapipe-gpu-x86_64" \
  USE_VENV=0 JOBS=4 \
  tools/build_mediapipe_gpu.sh'
```

## Jetson (JetPack 6 / Ubuntu 22.04)

Run the same script natively on the Jetson. Python 3.10, OpenCV, EGL/GLES
headers, Java 11, Git, curl, zip and a C++ compiler must be installed.
The script installs a private Clang 18 toolchain under `BUILD_ROOT/toolchains`;
it does not modify the JetPack system Python, CUDA, or PyTorch installation.

```bash
PYTHON_BIN=python3 \
BUILD_ROOT="$HOME/build/mediapipe-gpu" \
JOBS=8 \
tools/build_mediapipe_gpu.sh
```

For the kiosk runtime, use the Miniforge `geekseek` environment (Python 3.10)
rather than the build venv. Install the wheel without asking pip for the
unavailable aarch64 `opencv-contrib-python` dependency; JetPack's system
OpenCV extension is linked into the environment instead:

```bash
curl -fsSLo /tmp/Miniforge3.sh \
  https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh
bash /tmp/Miniforge3.sh -b -p "$HOME/miniforge3"
source "$HOME/miniforge3/etc/profile.d/conda.sh"

conda create -n geekseek python=3.10 pip numpy=1.26 \
  absl-py certifi flatbuffers matplotlib python-sounddevice

SITE_DIR="$(conda run -n geekseek python -c \
  'import site; print(site.getsitepackages()[0])')"
ln -s /usr/lib/python3/dist-packages/cv2.cpython-310-aarch64-linux-gnu.so \
  "$SITE_DIR/cv2.cpython-310-aarch64-linux-gnu.so"
conda run -n geekseek python -m pip install --no-deps \
  "$HOME"/build/mediapipe-gpu/wheelhouse/mediapipe-0.10.35+gpu-*-linux_aarch64.whl

ENV_PREFIX="$(conda run -n geekseek python -c 'import sys; print(sys.prefix)')"
mkdir -p "$ENV_PREFIX/etc/conda/activate.d" \
  "$ENV_PREFIX/etc/conda/deactivate.d"
cp tools/conda/activate.d/geekseek-jetson-gpu.sh \
  "$ENV_PREFIX/etc/conda/activate.d/"
cp tools/conda/deactivate.d/geekseek-jetson-gpu.sh \
  "$ENV_PREFIX/etc/conda/deactivate.d/"

conda run -n geekseek python tools/check_mediapipe_gpu.py
```

The checker is intentionally strict: it does not silently fall back to CPU.
It creates a two-person PoseLandmarker and runs one blank frame through it.

### Everything else must be installed *inside* this env, not `--user`

`pip install --no-deps` above only covers the GPU wheel itself. The rest of
the runtime — the editable `geekseek` package, `fastapi`, `uvicorn[standard]`,
`httpx`, `pygame` (for `runtime.debug_window`) — still needs to land in
`$ENV_PREFIX/lib/python3.10/site-packages`:

```bash
conda run -n geekseek python -m pip install --no-deps -e .
conda run -n geekseek python -m pip install \
  'fastapi>=0.115' 'uvicorn[standard]>=0.34' 'httpx>=0.28' pygame
```

Do **not** `pip install --user` any of these on the same machine. A user-site
package (`~/.local/lib/python3.10/site-packages`) shadows an env-installed one
of the same name, since user-site is searched first by default — so a stray
`pip install --user mediapipe` (the plain CPU PyPI wheel) will silently make
`import mediapipe` resolve to the CPU build even inside this env, and the
checker above will fail. `tools/conda/activate.d/geekseek-jetson-gpu.sh` sets
`PYTHONNOUSERSITE=1` while this env is active specifically to prevent that —
but that also means anything only installed with `--user` (not into this
env's own site-packages) becomes invisible while the env is active, which is
why every runtime dependency must be installed into the env directly, as
above.
