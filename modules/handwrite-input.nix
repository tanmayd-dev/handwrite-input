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

    users = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      example = [ "alice" ];
      description = ''
        Sessions allowed to drive the daemon. Members are added to
        {option}`programs.ydotool.group`, which also requires a new login
        to take effect.
      '';
    };

    injector = lib.mkOption {
      type = lib.types.enum [
        "auto"
        "ydotool"
        "wtype"
      ];
      default = "auto";
      description = ''
        Tool used to type recognised text into the focused window.

        `ydotool` writes through {file}`/dev/uinput`, so the keystrokes look like
        an ordinary keyboard to every client, XWayland ones included. `wtype`
        drives the Wayland virtual-keyboard protocol with a throwaway keymap that
        it removes again on exit; clients which resolve that keymap late
        translate the keycodes with the physical keymap instead, so recognised
        text arrives as digits and stray symbols. `auto` prefers ydotool when its
        daemon is reachable and falls back to wtype.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    # ydotool types through /dev/uinput; wtype needs nothing beyond the compositor.
    hardware.uinput.enable = lib.mkDefault true;
    programs.ydotool.enable = lib.mkDefault true;

    users.users = lib.mkIf (cfg.injector != "wtype") (
      lib.genAttrs cfg.users (user: {
        extraGroups = [ config.programs.ydotool.group ];
      })
    );

    systemd.user.services.handwrite-input = {
      description = "Handwrite Input daemon";
      wantedBy = [ "graphical-session.target" ];
      environment.HANDWRITE_INJECTOR = cfg.injector;
      serviceConfig = {
        ExecStart = "${cfg.package}/bin/handwrite-input";
        Restart = "on-failure";
        RestartSec = 3;
      };
    };

    environment.systemPackages = [ cfg.package ];
  };
}
