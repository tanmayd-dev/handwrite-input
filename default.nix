{
  pkgs ? import <nixpkgs> { },
}:

let
  inherit (pkgs) lib;
  py = pkgs.python3Packages;

  # transformers 4.46.x predates huggingface-hub 1.0, which swapped its HTTP
  # client from `requests` to `httpx`. httpx renamed the `allow_redirects`
  # kwarg to `follow_redirects`, so transformers' `has_file()` blows up with:
  #   TypeError: Client.head() got an unexpected keyword argument 'allow_redirects'
  # surfaced as "Can't load the model for '<repo>'". Only *weight* loading takes
  # that code path — tokenizer/config loading never does.
  # Pinning hub <1.0 is not viable: nixpkgs' tokenizers declares hub 1.30 as a
  # dependency, so a downgrade yields a duplicate-package closure.
  transformers446 = py.transformers.overridePythonAttrs (old: {
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
    postPatch = ''
      # transformers 5.x pins these bounds in dependency_versions_table.py and
      # enforces them at import time, so relaxing METADATA alone is not enough.
      substituteInPlace src/transformers/dependency_versions_table.py \
        --replace-fail 'tokenizers>=0.20,<0.21' 'tokenizers>=0.20' \
        --replace-fail 'huggingface-hub>=0.23.2,<1.0' 'huggingface-hub>=0.23.2'

      # huggingface-hub >= 1.0 returns an httpx session; transformers 4.46 calls
      # it with `requests` kwargs (`proxies`, `allow_redirects`) and catches
      # `requests.exceptions.*`. Use a plain requests session for that one call.
      substituteInPlace src/transformers/utils/hub.py \
        --replace-fail 'get_session().head(' 'requests.Session().head('
    '';
    # transformers 4.x imports `requests`; the 5.x expression no longer lists it.
    dependencies = (old.dependencies or [ ]) ++ [ py.requests ];
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

  typelibPath = lib.makeSearchPath "lib/girepository-1.0" [
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
    meta = with lib; {
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
      --prefix PATH : "${
        lib.makeBinPath [
          pkgs.wtype
          pkgs.ydotool
        ]
      }" \
      --set GI_TYPELIB_PATH "${typelibPath}"

    # Client
    makeWrapper ${pythonEnv}/bin/python $out/bin/handwrite-input-client \
      --add-flags "$src/handwrite-input-client.py" \
      --set GI_TYPELIB_PATH "${typelibPath}"
  ''
