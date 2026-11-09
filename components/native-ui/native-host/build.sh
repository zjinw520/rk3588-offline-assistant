#!/bin/sh
# Run on the Ubuntu SDK host, not on Windows or on the board.
set -eu
export LC_ALL=C

SOURCE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
HOST_DIR="${RK3588_HOST_DIR:-/home/alientek/atk_dlrk3588_linux5.10/buildroot/output/alientek_rk3588/host}"
PREFIX="$HOST_DIR/bin/aarch64-buildroot-linux-gnu-"
if [ ! -x "${PREFIX}g++" ]; then
    echo "Cross compiler missing: ${PREFIX}g++" >&2
    echo "Set RK3588_HOST_DIR to this board's Buildroot host directory." >&2
    exit 1
fi

QMAKE="${RK3588_QMAKE:-}"
if [ -z "$QMAKE" ]; then
    for candidate in "$HOST_DIR/bin/qmake" "$HOST_DIR/usr/bin/qmake" "$HOST_DIR/lib/qt5/bin/qmake"; do
        if [ -x "$candidate" ]; then
            QMAKE="$candidate"
            break
        fi
    done
fi
if [ -z "$QMAKE" ] || [ ! -x "$QMAKE" ]; then
    echo "SDK qmake not found. Please report the following output:" >&2
    find "$HOST_DIR" -maxdepth 6 -name 'qmake*' -print
    exit 1
fi

export PATH="$HOST_DIR/bin:$PATH"
machine="$("${PREFIX}g++" -dumpmachine)"
case "$machine" in
    aarch64*) ;;
    *) echo "Refusing non-AArch64 compiler: $machine" >&2; exit 1 ;;
esac
version="$("$QMAKE" -query QT_VERSION)"
case "$version" in
    5.15.*) ;;
    *) echo "Expected this board's Qt 5.15 SDK; qmake reports $version" >&2; exit 1 ;;
esac
echo "qmake=$QMAKE"
echo "Qt=$version compiler=$machine"

BUILD_DIR="$SOURCE_DIR/build"
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"
"$QMAKE" "$SOURCE_DIR/rk3588-ai.pro" \
    "QMAKE_CC=${PREFIX}gcc" "QMAKE_CXX=${PREFIX}g++" "QMAKE_LINK=${PREFIX}g++"
make -j"${RK3588_BUILD_JOBS:-2}"

"${PREFIX}readelf" -h rk3588-ai > elf-header.txt
"${PREFIX}readelf" --version-info rk3588-ai > elf-versions.txt
if ! grep -q 'Machine:.*AArch64' elf-header.txt; then
    echo "Unexpected ELF architecture; see $BUILD_DIR/elf-header.txt" >&2
    exit 1
fi
needed="$(sed -n 's/.*Name: GLIBC_\([0-9.]*\).*/\1/p' elf-versions.txt | sort -Vu | tail -n 1)"
if [ -z "$needed" ]; then
    echo "Cannot determine GLIBC requirements; please report elf-versions.txt" >&2
    exit 1
fi
echo "Highest required GLIBC symbol version: $needed; board has 2.37."
highest="$(printf '%s\n' 2.37 "$needed" | sort -V | tail -n 1)"
if [ "$highest" != 2.37 ]; then
    echo "Do not deploy: this SDK produced an executable requiring GLIBC $needed." >&2
    exit 1
fi
"${PREFIX}readelf" -d rk3588-ai > elf-dynamic.txt
file rk3588-ai
sha256sum rk3588-ai
echo "BUILD_OK: $BUILD_DIR/rk3588-ai"
echo "Qt shared libraries must also be checked with ldd on the board before installation."
