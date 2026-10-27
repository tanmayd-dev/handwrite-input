# Maintainer: Tanmay Deshpande <work.tanmaydeshpande@gmail.com>
#
# VCS package: builds whatever is at the tip of the canonical GitHub repo.
# Recognition itself is an online call to Google Input Tools, so there is no
# model to download or bundle and arch=any is accurate.

pkgname=handwrite-input-git
pkgver=0.1.0.r37.g333abea
pkgrel=1
pkgdesc='System-wide handwriting input overlay for Wayland (Google Input Tools backend)'
arch=('any')
url='https://github.com/tanmayd-dev/handwrite-input'
license=('MIT')
# Runtime imports, derived from src/*.py:
#   gi.repository Gtk/Gdk/GLib/GtkLayerShell  -> python-gobject + gtk3 + gtk-layer-shell
#   cairo                                     -> python-cairo
#   json, socket, urllib, subprocess, ...     -> stdlib (python)
depends=(
  'python'
  'python-gobject'
  'python-cairo'
  'gtk3'
  'gtk-layer-shell'
)
# handwrite_inject.py shells out to one of these.
optdepends=(
  'ydotool: preferred injector, types through /dev/uinput (works in every client)'
  'wtype: fallback injector, Wayland virtual-keyboard protocol'
)
makedepends=('git')
source=("$pkgname::git+$url.git")
sha256sums=('SKIP')

pkgver() {
  cd "$pkgname"
  printf '0.1.0.r%s.g%s' \
    "$(git rev-list --count HEAD)" \
    "$(git rev-parse --short HEAD)"
}

check() {
  cd "$pkgname"
  # Syntax-only check; the GTK imports are exercised at runtime.
  python3 -m py_compile src/*.py
}

package() {
  cd "$pkgname"

  # The daemon and client import their siblings (handwrite_config,
  # handwrite_google, handwrite_inject) from sys.path[0], so all Python files
  # have to sit in one directory. /usr/bin is not a Python package directory.
  local libdir="$pkgdir/usr/lib/handwrite-input"
  install -d "$libdir" "$pkgdir/usr/bin"

  install -m644 \
    src/handwrite_config.py \
    src/handwrite_google.py \
    src/handwrite_inject.py \
    "$libdir/"
  install -m755 \
    src/handwrite-input.py \
    src/handwrite-input-client.py \
    "$libdir/"

  # Thin wrappers rather than symlinks: os.path.abspath(__file__) does not
  # resolve symlinks, so the scripts' sys.path insert would point at /usr/bin.
  local prog
  for prog in handwrite-input handwrite-input-client; do
    printf '#!/bin/sh\nexec /usr/bin/python3 /usr/lib/handwrite-input/%s.py "$@"\n' "$prog" \
      > "$pkgdir/usr/bin/$prog"
    chmod 755 "$pkgdir/usr/bin/$prog"
  done

  install -Dm644 packaging/handwrite-input.service \
    "$pkgdir/usr/lib/systemd/user/handwrite-input.service"

  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
