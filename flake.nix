{
  description = "System-wide handwriting input for Wayland";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }: {
    nixosModules.handwrite-input = import ./modules/handwrite-input.nix;

    packages.x86_64-linux.default = nixpkgs.legacyPackages.x86_64-linux.callPackage ./default.nix {};
  };
}
