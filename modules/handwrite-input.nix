{ config, lib, pkgs, ... }:

let
  cfg = config.services.handwrite-input;
in
{
  options.services.handwrite-input = {
    enable = lib.mkEnableOption "Handwrite Input — system-wide handwriting recognition for Wayland";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.handwrite-input;
      defaultText = lib.literalExpression "pkgs.handwrite-input";
      description = "The handwrite-input package to use.";
    };

    settings = lib.mkOption {
      type = lib.types.submodule {
        options = {
          language = lib.mkOption {
            type = lib.types.str;
            default = "eng";
            description = "Tesseract language code (e.g., eng, deu, fra).";
          };
          debounceMs = lib.mkOption {
            type = lib.types.int;
            default = 1500;
            description = "Milliseconds to wait after the last stroke before recognizing.";
          };
          windowWidth = lib.mkOption {
            type = lib.types.int;
            default = 600;
            description = "Overlay window width in pixels.";
          };
          windowHeight = lib.mkOption {
            type = lib.types.int;
            default = 300;
            description = "Overlay window height in pixels.";
          };
        };
      };
      default = { };
      description = "Handwrite Input settings.";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.user.services.handwrite-input = {
      description = "Handwrite Input daemon";
      wantedBy = [ "graphical-session.target" ];
      serviceConfig = {
        ExecStart = "${cfg.package}/bin/handwrite-input";
        Restart = "on-failure";
        RestartSec = 3;
      };
    };

    environment.systemPackages = [ cfg.package ];
  };
}
