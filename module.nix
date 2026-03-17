# Called as: import ./module.nix mkPackages
mkPackages:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.dmarc-analyzer;
  packages = mkPackages pkgs;
  dataDir = "/var/lib/dmarc-analyzer";
  user = "dmarc-analyzer";
  group = "dmarc-analyzer";
in
{

  options.services.dmarc-analyzer = {

    enable = lib.mkEnableOption "DMARC analyzer";

    port = lib.mkOption {
      type = lib.types.port;
      default = 8741;
      description = ''
        Port the HTTP server listens on. Point your reverse proxy here.
      '';
    };

    listenHost = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      example = "0.0.0.0";
      description = ''
        Address the HTTP server binds to. Default is loopback-only.
        Set to a LAN address if your reverse proxy is on a different machine.
        Firewall rules are your responsibility in that case.
      '';
    };

    mailDir = lib.mkOption {
      type = lib.types.str;
      example = "/var/vmail/example.com/postmaster/mail";
      description = ''
        Maildir or mbox path containing DMARC aggregate report emails.
      '';
    };

    scanUser = lib.mkOption {
      type = lib.types.str;
      default = user;
      example = "virtualMail";
      description = ''
        Unix user the scanner runs as. Must have read access to mailDir.
        nixos-mailserver sets Maildir permissions to 700 owned by its vmail
        user, making group-based access impossible — set this to that user.
        Example: config.mailserver.vmailUserName
      '';
    };

    scanInterval = lib.mkOption {
      type = lib.types.str;
      default = "15min";
      description = ''
        How often to scan. systemd OnUnitActiveSec format: \"5min\", \"1h\", etc.
      '';
    };

    maxReports = lib.mkOption {
      type = lib.types.int;
      default = 200;
      description = "Maximum number of reports kept in reports.json.";
    };

  };

  config = lib.mkIf cfg.enable {

    users.users.${user} = {
      isSystemUser = true;
      group = group;
      home = dataDir;
      description = "DMARC analyzer service user";
    };

    users.groups.${group} = {
      # scanUser (e.g. virtualMail) needs group membership to write to dataDir.
      members = lib.optional (cfg.scanUser != user) cfg.scanUser;
    };

    systemd.services.dmarc-server = {
      description = "DMARC analyzer HTTP server";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      environment = {
        DMARC_WEB_ROOT = "${packages.dmarc-analyzer}/share/dmarc-analyzer";
        DMARC_DATA_DIR = "${dataDir}/data";
        DMARC_PORT = toString cfg.port;
        DMARC_HOST = cfg.listenHost;
      };
      serviceConfig = {
        ExecStart = "${packages.dmarc-server}/bin/dmarc-server";
        User = user;
        Group = group;
        Restart = "on-failure";
        RestartSec = "5s";
        StateDirectory = "dmarc-analyzer dmarc-analyzer/data";
        StateDirectoryMode = "0770";
        ReadOnlyPaths = [ "${packages.dmarc-analyzer}/share/dmarc-analyzer" ];
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
      };
    };

    systemd.services.dmarc-scanner = {
      description = "DMARC report mail scanner";
      after = [ "network.target" ];
      serviceConfig = {
        Type = "oneshot";
        User = cfg.scanUser;
        Group = group;
        UMask = "0007";
        StateDirectory = "dmarc-analyzer dmarc-analyzer/data";
        StateDirectoryMode = "0770";
        ExecStart = lib.escapeShellArgs [
          "${packages.dmarc-scanner}/bin/dmarc-scanner"
          "--maildir"
          cfg.mailDir
          "--output"
          "${dataDir}/data/reports.json"
          "--state"
          "${dataDir}/seen.json"
          "--max-reports"
          (toString cfg.maxReports)
        ];
        ReadOnlyPaths = [ cfg.mailDir ];
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
      };
    };

    systemd.timers.dmarc-scanner = {
      description = "DMARC scanner timer";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "2min";
        OnUnitActiveSec = cfg.scanInterval;
        Persistent = true;
      };
    };

  };
}
