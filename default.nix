{
  pkgs ? import <nixpkgs> { },
}:

let
  # transformers 5.x ignores use_fast=False and cannot load TrOCR's Roberta tokenizer.
  # Pin 4.46.3 and relax deps so we keep nixpkgs tokenizers/huggingface-hub
  # (do not pin tokenizers — its sdist layout breaks the Nix Rust vendor unpack).
  transformers446 = pkgs.python3Packages.transformers.overridePythonAttrs (old: {
    version = "4.46.3";
    src = pkgs.fetchPypi {
      pname = "transformers";
      version = "4.46.3";
      sha256 = "1k3jbwlk3v99nyskrgm9gjq5h8059dzq73pzmy13xqrzjjpb7r4f";
    };
    patches = [ ];
    pythonRelaxDeps = [
      "tokenizers"
      "huggingface-hub"
      "safetensors"
    ];
    propagatedBuildInputs = (old.propagatedBuildInputs or [ ]) ++ [
      pkgs.python3Packages.requests
    ];
    postPatch = ''
      substituteInPlace src/transformers/dependency_versions_table.py \
        --replace-fail 'tokenizers>=0.20,<0.21' 'tokenizers>=0.20' \
        --replace-fail 'huggingface-hub>=0.23.2,<1.0' 'huggingface-hub>=0.23.2'
    '';
    doCheck = false;
    pythonImportsCheck = [ "transformers" ];
  });
  pythonEnv = pkgs.python3.withPackages (
    ps: with ps; [
      pygobject3
      pycairo
      pillow
      transformers446
      torch
      sentencepiece
      tiktoken
    ]
  );
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
pkgs.runCommand "handwrite-input-0.1.0"
  {
    src = ./src;
    nativeBuildInputs = [ pkgs.makeWrapper ];
    meta = with pkgs.lib; {
      description = "System-wide handwriting input for Wayland";
      license = licenses.mit;
      platforms = platforms.linux;
    };
  }
  ''
    mkdir -p $out/bin

    # Main daemon
    makeWrapper ${pythonEnv}/bin/python $out/bin/handwrite-input \
      --add-flags "$src/handwrite-input.py" \
      --prefix PATH : "${pkgs.lib.makeBinPath [ pkgs.wtype ]}" \
      --set GI_TYPELIB_PATH "${typelibPath}"

    # Client
    makeWrapper ${pythonEnv}/bin/python $out/bin/handwrite-input-client \
      --add-flags "$src/handwrite-input-client.py" \
      --set GI_TYPELIB_PATH "${typelibPath}"
  ''
