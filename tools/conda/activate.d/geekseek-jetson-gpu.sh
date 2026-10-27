#!/usr/bin/env bash

# A stray `pip install --user mediapipe` (CPU wheel) elsewhere on the machine
# lands in ~/.local/lib/pythonX.Y/site-packages, which Python puts ahead of
# this env's own site-packages — silently shadowing the GPU wheel installed
# here with the CPU one. Disable user-site lookups for this env so `import
# mediapipe` always resolves to the wheel actually installed in $CONDA_PREFIX.
_GEEKSEEK_PREV_PYTHONNOUSERSITE="${PYTHONNOUSERSITE-}"
export PYTHONNOUSERSITE=1

# JetPack exposes NVIDIA EGL/GLES through vendor libraries. Going through the
# generic GLVND stubs can yield a valid EGL context whose GL calls are null on
# Jetson, so prefer the vendor libraries without modifying system symlinks.
_GEEKSEEK_PREV_LD_LIBRARY_PATH="${LD_LIBRARY_PATH-}"
_geekseek_build_root="${GEEKSEEK_MEDIAPIPE_BUILD_ROOT:-$HOME/build/mediapipe-gpu}"
export LD_LIBRARY_PATH="$_geekseek_build_root/nvidia-egl${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
unset _geekseek_build_root

# Tailscale SSH does not forward DISPLAY. Reuse the local kiosk session when
# one exists so EGL can initialize consistently after boot.
if [[ -z "${DISPLAY-}" ]]; then
  for _geekseek_x_socket in /tmp/.X11-unix/X*; do
    if [[ -S "$_geekseek_x_socket" ]]; then
      _GEEKSEEK_SET_DISPLAY=1
      export DISPLAY=":${_geekseek_x_socket##*X}"
      break
    fi
  done
  unset _geekseek_x_socket
fi

_geekseek_xauthority="/run/user/$(id -u)/gdm/Xauthority"
if [[ -z "${XAUTHORITY-}" && -f "$_geekseek_xauthority" ]]; then
  _GEEKSEEK_SET_XAUTHORITY=1
  export XAUTHORITY="$_geekseek_xauthority"
fi
unset _geekseek_xauthority
