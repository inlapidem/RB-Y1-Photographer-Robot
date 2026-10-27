#!/usr/bin/env bash
set -euo pipefail

# Build a GPU-enabled MediaPipe Tasks wheel for the machine executing this
# script.  Run it once on Ubuntu x86_64 and once natively on Jetson aarch64;
# Python wheels are architecture-specific.

MEDIAPIPE_TAG="${MEDIAPIPE_TAG:-v0.10.35}"
BUILD_ROOT="${BUILD_ROOT:-${XDG_CACHE_HOME:-$HOME/.cache}/geekseek/mediapipe-gpu}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOBS="${JOBS:-2}"
USE_VENV="${USE_VENV:-1}"

case "$(uname -m)" in
  x86_64)
    bazel_arch="x86_64"
    host_cc="${CC:-$(command -v clang || command -v gcc || true)}"
    host_cxx="${CXX:-$(command -v clang++ || command -v g++ || true)}"
    if [[ -z "$host_cc" || -z "$host_cxx" ]] || \
      ! printf 'int main(void) { return 0; }\n' | "$host_cc" -x c - -c -o /dev/null -mavx512fp16 -mavxvnniint8 2>/dev/null; then
      echo "Ubuntu x86_64 requires Clang 18+ or GCC 13+ (set CC and CXX if needed)." >&2
      exit 2
    fi

    # Conda's GCC sysroot does not ship EGL/GLES headers, while MediaPipe's
    # Linux GPU build needs the host Mesa headers. Keep them under the
    # compiler's already-declared builtin include root so Bazel 7's strict
    # include scanner accepts them without weakening sandbox checks.
    host_sysroot="$("$host_cc" -print-sysroot)"
    if [[ -n "$host_sysroot" && -d "$host_sysroot/usr/include" ]]; then
      for header_dir in EGL GLES2 GLES3 KHR; do
        if [[ -d "/usr/include/$header_dir" && ! -e "$host_sysroot/usr/include/$header_dir" ]]; then
          cp -a "/usr/include/$header_dir" "$host_sysroot/usr/include/"
        fi
      done
    fi
    ;;
  aarch64|arm64)
    bazel_arch="arm64"
    ;;
  *) echo "Unsupported architecture: $(uname -m)" >&2; exit 2 ;;
esac

mkdir -p "$BUILD_ROOT/bin" "$BUILD_ROOT/wheelhouse"

if [[ "$bazel_arch" == "arm64" ]]; then
  llvm_root="$BUILD_ROOT/toolchains/clang-llvm-18"
  if [[ ! -x "$llvm_root/bin/clang++" ]]; then
    mkdir -p "$BUILD_ROOT/toolchains" "$llvm_root"
    llvm_archive="$BUILD_ROOT/toolchains/clang-llvm-18.tar.xz"
    curl -fL --retry 3 \
      "https://github.com/llvm/llvm-project/releases/download/llvmorg-18.1.8/clang%2Bllvm-18.1.8-aarch64-linux-gnu.tar.xz" \
      -o "$llvm_archive"
    tar -xJf "$llvm_archive" -C "$llvm_root" --strip-components=1
    unlink "$llvm_archive"
  fi
  host_cc="$llvm_root/bin/clang"
  host_cxx="$llvm_root/bin/clang++"

  # JetPack's GLVND stubs can create an EGL context while leaving GLES calls
  # undispatched. Provide private, build-local names for the NVIDIA vendor
  # libraries; the conda activation hook prepends this directory at runtime.
  nvidia_egl_root="/usr/lib/aarch64-linux-gnu/tegra-egl"
  if [[ -f "$nvidia_egl_root/libEGL_nvidia.so.0" && -f "$nvidia_egl_root/libGLESv2_nvidia.so.2" ]]; then
    mkdir -p "$BUILD_ROOT/nvidia-egl"
    ln -sfn "$nvidia_egl_root/libEGL_nvidia.so.0" "$BUILD_ROOT/nvidia-egl/libEGL.so.1"
    ln -sfn "$nvidia_egl_root/libGLESv2_nvidia.so.2" "$BUILD_ROOT/nvidia-egl/libGLESv2.so.2"
  fi
fi

if [[ "$USE_VENV" == "0" ]]; then
  build_python="$PYTHON_BIN"
  if ! "$build_python" -c 'import numpy, setuptools, wheel' 2>/dev/null; then
    echo "The selected Python needs numpy, setuptools and wheel." >&2
    exit 2
  fi
else
  if [[ ! -x "$BUILD_ROOT/venv/bin/python" ]]; then
    if "$PYTHON_BIN" -m venv "$BUILD_ROOT/venv" 2>/dev/null; then
      :
    elif "$PYTHON_BIN" -m virtualenv "$BUILD_ROOT/venv" 2>/dev/null; then
      :
    else
      echo "Cannot create a virtual environment. Install python3-venv or virtualenv." >&2
      exit 2
    fi
  fi
  build_python="$BUILD_ROOT/venv/bin/python"
  "$build_python" -m pip install --upgrade pip setuptools wheel "numpy<2"
fi

bazel="$BUILD_ROOT/bin/bazel"
if [[ ! -x "$bazel" ]]; then
  curl -fL \
    "https://github.com/bazelbuild/bazel/releases/download/7.4.1/bazel-7.4.1-linux-${bazel_arch}" \
    -o "$bazel"
  chmod +x "$bazel"
fi

source_dir="$BUILD_ROOT/src"
if [[ ! -d "$source_dir/.git" ]]; then
  git clone --depth 1 --branch "$MEDIAPIPE_TAG" \
    https://github.com/google-ai-edge/mediapipe.git "$source_dir"
fi

cd "$source_dir"
expected_commit="$(git rev-list -n 1 "$MEDIAPIPE_TAG")"
if [[ "$(git rev-parse HEAD)" != "$expected_commit" ]]; then
  echo "$source_dir exists but is not checked out at $MEDIAPIPE_TAG" >&2
  exit 2
fi

source_patch="$SCRIPT_DIR/mediapipe-v0.10.35-wheel.patch"
if git apply --check "$source_patch" 2>/dev/null; then
  git apply "$source_patch"
elif ! git apply --reverse --check "$source_patch" 2>/dev/null; then
  echo "MediaPipe source has unexpected changes; cannot apply $source_patch" >&2
  exit 2
fi

bazelrc="$source_dir/.bazelrc.geekseek-gpu"
printf '%s\n' \
  "startup --host_jvm_args=-Xmx2048m" \
  "build --jobs=$JOBS" \
  "build --repo_env=CC=$host_cc" \
  "build --repo_env=CXX=$host_cxx" \
  "build --action_env=CC=$host_cc" \
  "build --action_env=CXX=$host_cxx" >"$bazelrc"
if [[ "$bazel_arch" == "x86_64" ]]; then
  printf '%s\n' \
    "build --copt=-idirafter/usr/include" \
    "build --linkopt=-L/usr/lib/x86_64-linux-gnu" >>"$bazelrc"
fi
if ! grep -q '^try-import %workspace%/.bazelrc.geekseek-gpu$' .bazelrc; then
  printf '\ntry-import %%workspace%%/.bazelrc.geekseek-gpu\n' >>.bazelrc
fi

# setup.py defaults to a GPU-disabled build. The build_py option tells it to
# link the host OpenCV installation instead of rebuilding OpenCV from source.
export PATH="$BUILD_ROOT/bin:$PATH"
export MEDIAPIPE_DISABLE_GPU=0
export HERMETIC_PYTHON_VERSION="$("$build_python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
wheel_version="${MEDIAPIPE_TAG#v}"
# setup.py temporarily appends public imports to this file and restores it only
# after a successful build. Interrupted/retried builds otherwise append the
# same block repeatedly and produce a wheel that fails on its second import.
git show HEAD:mediapipe/__init__.py >mediapipe/__init__.py
unlink mediapipe/__init__.py.backup 2>/dev/null || true
sed -Ei "s/^__version__ = .*/__version__ = '${wheel_version}+gpu'/" setup.py

"$build_python" setup.py build_py --force --link-opencv bdist_wheel
cp -v dist/mediapipe-"${wheel_version}+gpu"-*.whl "$BUILD_ROOT/wheelhouse/"

echo "GPU-enabled MediaPipe wheel(s):"
find "$BUILD_ROOT/wheelhouse" -maxdepth 1 -name '*.whl' -print
