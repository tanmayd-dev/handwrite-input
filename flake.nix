{
  description = "System-wide handwriting input for Wayland";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }: {
    nixosModules.handwrite-input =
      {
        config,
        pkgs,
        lib,
        ...
      }:
      {
        imports = [ ./modules/handwrite-input.nix ];
        services.handwrite-input.package = self.packages.x86_64-linux.default;
      };

    packages.x86_64-linux.default = nixpkgs.legacyPackages.x86_64-linux.callPackage ./default.nix { };
  };
}
