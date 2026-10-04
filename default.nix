{ pkgs ? import <nixpkgs> {} }:

let
  pythonEnv = pkgs.python3.withPackages (ps: with ps; [
    pygobject3
    pycairo
    pillow
  ]);
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
    --prefix PATH : "${pkgs.lib.makeBinPath [ pkgs.tesseract pkgs.wtype ]}"

  # Client
  makeWrapper ${pythonEnv}/bin/python $out/bin/handwrite-input-client \
    --add-flags "$src/handwrite-input-client.py"
''
