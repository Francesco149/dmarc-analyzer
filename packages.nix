# Called as: mkPackages pkgs
# All packages are architecture-independent — no compiled code.
pkgs: rec {

  dmarc-analyzer = pkgs.stdenvNoCC.mkDerivation {
    pname = "dmarc-analyzer";
    version = "2.3.3";
    src = ./dmarc-feed.html;
    dontUnpack = true;
    dontBuild = true;
    installPhase = ''
      mkdir -p $out/share/dmarc-analyzer
      cp $src $out/share/dmarc-analyzer/index.html
    '';
    meta.description = "DMARC aggregate report analyzer — offline frontend";
  };

  dmarc-scanner = pkgs.stdenvNoCC.mkDerivation {
    pname = "dmarc-scanner";
    version = "2.1.2";
    src = ./dmarc-scanner.py;
    dontUnpack = true;
    dontBuild = true;
    installPhase = ''
      mkdir -p $out/bin
      cp $src $out/bin/dmarc-scanner
      chmod +x $out/bin/dmarc-scanner
      sed -i '1s|.*|#!${pkgs.python3}/bin/python3|' $out/bin/dmarc-scanner
    '';
    meta.description = "DMARC mail scanner — extracts reports from Maildir";
  };

  dmarc-server = pkgs.stdenvNoCC.mkDerivation {
    pname = "dmarc-server";
    version = "2.0.1";
    src = ./dmarc-server.py;
    dontUnpack = true;
    dontBuild = true;
    installPhase = ''
      mkdir -p $out/bin
      cp $src $out/bin/dmarc-server
      chmod +x $out/bin/dmarc-server
      sed -i '1s|.*|#!${pkgs.python3}/bin/python3|' $out/bin/dmarc-server
    '';
    meta.description = "DMARC analyzer HTTP server — binds to 127.0.0.1 only";
  };

  default = dmarc-analyzer;
}
