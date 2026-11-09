#!/bin/sh
# Run on the matching Ubuntu SDK host, not on the board or Windows.
set -eu
export LC_ALL=C

SOURCE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
HOST_DIR="${RK3588_HOST_DIR:-/home/alientek/atk_dlrk3588_linux5.10/buildroot/output/alientek_rk3588/host}"
PREFIX="$HOST_DIR/bin/aarch64-buildroot-linux-gnu-"
if [ ! -x "${PREFIX}g++" ]; then
    echo "Cross compiler missing: ${PREFIX}g++" >&2
    exit 1
fi
QMAKE="${RK3588_QMAKE:-}"
if [ -z "$QMAKE" ]; then
    for candidate in "$HOST_DIR/bin/qmake" "$HOST_DIR/usr/bin/qmake" "$HOST_DIR/lib/qt5/bin/qmake"; do
        if [ -x "$candidate" ]; then QMAKE="$candidate"; break; fi
    done
fi
if [ -z "$QMAKE" ] || [ ! -x "$QMAKE" ]; then
    echo "Matching SDK qmake not found; set RK3588_HOST_DIR or RK3588_QMAKE." >&2
    exit 1
fi
export PATH="$HOST_DIR/bin:$PATH"
machine="$("${PREFIX}g++" -dumpmachine)"
case "$machine" in aarch64*) ;; *) echo "Refusing non-AArch64 compiler: $machine" >&2; exit 1 ;; esac
version="$("$QMAKE" -query QT_VERSION)"
case "$version" in 5.15.*) ;; *) echo "Expected Qt 5.15 SDK; got $version" >&2; exit 1 ;; esac
echo "qmake=$QMAKE Qt=$version compiler=$machine"

mkdir -p "$SOURCE_DIR/build/library" "$SOURCE_DIR/build/selftest" "$SOURCE_DIR/dist"
cd "$SOURCE_DIR/build/library"
"$QMAKE" "$SOURCE_DIR/systemuiwake.pro" \
    "QMAKE_CC=${PREFIX}gcc" "QMAKE_CXX=${PREFIX}g++" "QMAKE_LINK=${PREFIX}g++"
make -j"${RK3588_BUILD_JOBS:-2}"
cd "$SOURCE_DIR/build/selftest"
"$QMAKE" "$SOURCE_DIR/systemui-wake-selftest.pro" \
    "QMAKE_CC=${PREFIX}gcc" "QMAKE_CXX=${PREFIX}g++" "QMAKE_LINK=${PREFIX}g++"
make -j"${RK3588_BUILD_JOBS:-2}"

for name in libsystemuiwake.so systemui-wake-selftest; do
    artifact="$SOURCE_DIR/dist/$name"
    "${PREFIX}readelf" -h "$artifact" > "$SOURCE_DIR/dist/$name.header.txt"
    "${PREFIX}readelf" --version-info "$artifact" > "$SOURCE_DIR/dist/$name.versions.txt"
    "${PREFIX}readelf" -d "$artifact" > "$SOURCE_DIR/dist/$name.dynamic.txt"
    if ! grep -q 'Machine:.*AArch64' "$SOURCE_DIR/dist/$name.header.txt"; then
        echo "Refusing unexpected architecture: $artifact" >&2
        exit 1
    fi
    needed="$(sed -n 's/.*Name: GLIBC_\([0-9.]*\).*/\1/p' "$SOURCE_DIR/dist/$name.versions.txt" | sort -Vu | tail -n 1)"
    if [ -z "$needed" ]; then
        echo "Cannot determine GLIBC requirements for $artifact" >&2
        exit 1
    fi
    highest="$(printf '%s\n' 2.37 "$needed" | sort -V | tail -n 1)"
    if [ "$highest" != 2.37 ]; then
        echo "Refusing GLIBC $needed requirement: $artifact" >&2
        exit 1
    fi
    echo "$name highest required GLIBC=$needed (board=2.37)"
    file "$artifact"
    sha256sum "$artifact"
done
echo "BUILD_OK: $SOURCE_DIR/dist/libsystemuiwake.so"
echo "BUILD_OK: $SOURCE_DIR/dist/systemui-wake-selftest"
echo "Not board-tested. Run ldd and the isolated selftest on the board before installation."
