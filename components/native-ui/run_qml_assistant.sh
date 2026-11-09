#!/bin/sh

# Run from SSH first. Register with SystemUI only after the screen test passes.
if [ -r /etc/profile ]; then
    . /etc/profile
fi

export QT_QPA_PLATFORM=wayland
export XDG_RUNTIME_DIR=/var/run
export WAYLAND_DISPLAY=wayland-0
export PYTHONUNBUFFERED=1

APP_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)" || exit 1
exec python3 "$APP_DIR/launch.py" "$@"
