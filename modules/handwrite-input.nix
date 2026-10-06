{ config, lib, ... }:

let
  cfg = config.services.handwrite-input;
in
{
  options.services.handwrite-input = {
    enable = lib.mkEnableOption "Handwrite Input — system-wide handwriting recognition for Wayland";

    package = lib.mkOption {
      type = lib.types.package;
      default = null;
      defaultText = lib.literalExpression "pkgs.handwrite-input";
      description = "The handwrite-input package to use.";
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
