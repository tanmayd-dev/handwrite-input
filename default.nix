{ pkgs ? import <nixpkgs> {} }:

let
  pythonEnv = pkgs.python3.withPackages (ps: with ps; [
    pygobject3
    pycairo
    pillow
  ]);
  typelibPath = pkgs.lib.makeSearchPath "lib/girepository-1.0" [
    pkgs.gtk3
    pkgs.glib
    pkgs.gdk-pixbuf
    pkgs.gobject-introspection
    pkgs.pango.out
    pkgs.cairo
    pkgs.harfbuzz
    pkgs.atk
    pkgs.at-spi2-atk
    pkgs.at-spi2-core
    pkgs.gsettings-desktop-schemas
    pkgs.gtk-layer-shell
    pkgs.libx11
    pkgs.libxext
    pkgs.libxrandr
    pkgs.libxcursor
    pkgs.libxi
    pkgs.libxinerama
    pkgs.libxcomposite
    pkgs.libxdamage
    pkgs.libxfixes
    pkgs.libxrender
    pkgs.libxtst
  ];
in
pkgs.runCommand "handwrite-input-0.1.0" {
  src = ./src;
  nativeBuildInputs = [ pkgs.makeWrapper ];
  meta = with pkgs.lib; {
    description = "System-wide handwriting input for Wayland";
    license = licenses.mit;
    platforms = platforms.linux;
  };
} ''
  mkdir -p $out/bin

  # Main daemon
  makeWrapper ${pythonEnv}/bin/python $out/bin/handwrite-input \
    --add-flags "$src/handwrite-input.py" \
    --prefix PATH : "${pkgs.lib.makeBinPath [ pkgs.tesseract pkgs.wtype ]}" \
    --set GI_TYPELIB_PATH "${typelibPath}"

  # Client
  makeWrapper ${pythonEnv}/bin/python $out/bin/handwrite-input-client \
    --add-flags "$src/handwrite-input-client.py" \
    --set GI_TYPELIB_PATH "${typelibPath}"
''
