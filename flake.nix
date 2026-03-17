{
  description = "DMARC aggregate report analyzer";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
        "x86_64-darwin"
      ];
      mkPackages = import ./packages.nix;
    in
    {

      packages = nixpkgs.lib.genAttrs systems (system: mkPackages nixpkgs.legacyPackages.${system});

      devShells = nixpkgs.lib.genAttrs systems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.nodejs
              pkgs.python3
            ];
            shellHook = "npm install";
          };
        }
      );

      nixosModules.dmarc-analyzer = import ./module.nix mkPackages;
    };
}
