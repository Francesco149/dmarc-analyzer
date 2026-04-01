#!/usr/bin/env python3
"""
dmarc-scanner — scan a Maildir for DMARC aggregate report emails,
extract and parse the XML attachments, write a reports.json feed.

Usage:
  dmarc-scanner --maildir /var/mail/postmaster --output /var/lib/dmarc-analyzer/reports.json
  dmarc-scanner --maildir /var/mail/postmaster --output /var/lib/dmarc-analyzer/reports.json --state /var/lib/dmarc-analyzer/seen.json
"""

import argparse
import email
import email.parser
import email.policy
import hashlib
import io
import json
import mailbox
import os
import sys
import zipfile
import gzip
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


# ── XML helpers ───────────────────────────────────────────────────────────────

def g(el, tag, default=""):
    """Get text content of first matching child tag."""
    node = el.find(tag)
    return node.text.strip() if node is not None and node.text else default

def ga(el, tag):
    return el.findall(tag)

def fmt_date(ts_str):
    try:
        ts = int(ts_str)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


KNOWN_ORGS = {
    ("209.85.", "74.125.", "64.233.", "66.249.", "72.14."): "Google",
    ("149.72.", "167.89.", "208.117.", "198.21."): "SendGrid",
    ("148.163.", "198.2.", "205.201."): "Mailchimp",
    ("185.12.80.", "199.255.192."): "Postmark",
}

def identify_org(ip):
    for prefixes, name in KNOWN_ORGS.items():
        if any(ip.startswith(p) for p in prefixes):
            return name
    # Rough heuristic for big cloud ranges
    for prefix in ("54.", "52.", "35.", "18."):
        if ip.startswith(prefix):
            return "AWS"
    for prefix in ("40.", "13.", "20."):
        if ip.startswith(prefix):
            return "Microsoft"
    return None


# ── DMARC XML parser ──────────────────────────────────────────────────────────

def parse_dmarc_xml(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"XML parse error: {e}")

    meta   = root.find("report_metadata")
    policy = root.find("policy_published")

    if meta is None or policy is None:
        raise ValueError("Missing report_metadata or policy_published")

    begin = g(meta, "date_range/begin")
    end   = g(meta, "date_range/end")

    report = {
        "report_id":  g(meta, "report_id"),
        "submitter":  g(meta, "org_name"),
        "email":      g(meta, "email"),
        "domain":     g(policy, "domain"),
        "policy":     g(policy, "p", "none"),
        "sp":         g(policy, "sp") or g(policy, "p", "none"),
        "pct":        g(policy, "pct", "100"),
        "adkim":      g(policy, "adkim", "r"),
        "aspf":       g(policy, "aspf", "r"),
        "date_begin": fmt_date(begin),
        "date_end":   fmt_date(end),
        "begin_ts":   begin,
        "end_ts":     end,
        "records":    [],
        "total":      0,
        "passed":     0,
        "failed":     0,
    }

    for rec in ga(root, "record"):
        row  = rec.find("row")
        if row is None:
            continue

        ip    = g(row, "source_ip")
        count = int(g(row, "count", "0") or 0)
        pe    = row.find("policy_evaluated")
        disp  = g(pe, "disposition", "none") if pe is not None else "none"
        dkim  = g(pe, "dkim", "fail")        if pe is not None else "fail"
        spf   = g(pe, "spf",  "fail")        if pe is not None else "fail"

        dmarc_pass = dkim == "pass" or spf == "pass"

        report["total"]  += count
        report["passed"] += count if dmarc_pass else 0
        report["failed"] += count if not dmarc_pass else 0

        record = {
            "source_ip":    ip,
            "org":          identify_org(ip),
            "count":        count,
            "disposition":  disp,
            "dkim":         dkim,
            "spf":          spf,
            "dmarc_pass":   dmarc_pass,
            "auth_results": [],
        }

        for auth in rec.findall("auth_results/dkim"):
            record["auth_results"].append({
                "type":   "dkim",
                "domain": g(auth, "domain"),
                "result": g(auth, "result"),
                "selector": g(auth, "selector"),
            })
        for auth in rec.findall("auth_results/spf"):
            record["auth_results"].append({
                "type":   "spf",
                "domain": g(auth, "domain"),
                "result": g(auth, "result"),
            })

        report["records"].append(record)

    total = report["total"] or 1
    report["pass_rate"] = round((report["passed"] / total) * 100)
    return report


# ── Attachment extraction ─────────────────────────────────────────────────────

def extract_xml_from_attachment(part):
    """
    Given a MIME part, try to extract DMARC XML.
    Handles: raw .xml, .zip containing .xml, .gz containing .xml.
    Returns xml bytes or None.
    """
    ctype    = part.get_content_type()
    fname    = part.get_filename() or ""
    payload  = part.get_payload(decode=True)
    if not payload:
        return None

    fname_lower = fname.lower()

    # Direct XML
    if fname_lower.endswith(".xml") or ctype in ("text/xml", "application/xml"):
        return payload

    # Gzipped XML
    if (
        fname_lower.endswith(".xml.gz") or 
        fname_lower.endswith(".gz") or 
        ctype in ("application/gzip", "application/x-gzip")
    ):
        try:
            return gzip.decompress(payload)
        except Exception:
            return None

    # ZIP containing XML
    if fname_lower.endswith(".zip") or ctype in (
        "application/zip", "application/x-zip-compressed",
        "application/x-zip", "application/octet-stream",
    ):
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
                if xml_names:
                    return zf.read(xml_names[0])
        except zipfile.BadZipFile:
            return None

    return None


def extract_reports_from_message(msg):
    """Walk MIME tree, return list of parsed report dicts."""
    reports = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        xml_bytes = extract_xml_from_attachment(part)
        if xml_bytes:
            try:
                report = parse_dmarc_xml(xml_bytes)
                reports.append(report)
            except ValueError as e:
                print(f"  [warn] Could not parse XML: {e}", file=sys.stderr)
    return reports


# ── Maildir / mbox scanning ───────────────────────────────────────────────────

def message_id(msg):
    """Stable identifier for a message."""
    mid = msg.get("Message-ID", "")
    if mid:
        return mid.strip()
    raw = (str(msg.get("Subject", "")) + str(msg.get("Date", ""))).encode()
    return hashlib.sha1(raw).hexdigest()


def _is_dmarc_subject(msg):
    subject = str(msg.get("Subject", "")).lower()
    return "report domain:" in subject or "dmarc" in subject


def scan_maildir(path):
    """Yield (msg_id, full_message) for Maildir messages with DMARC-like subjects.
    Uses a two-pass approach: read headers only first, then re-parse the full
    message only when the subject matches — keeps memory flat."""
    # Add policy to the header parser
    header_parser = email.parser.BytesHeaderParser(policy=email.policy.default)

    # Custom factory to parse full messages with modern default policy
    def _factory(f):
        return email.message_from_binary_file(f, policy=email.policy.default)

    mdir = mailbox.Maildir(path, factory=_factory, create=False)
    for key in mdir.keys():
        # Pass 1: headers only — cheap, doesn't load attachments
        with mdir.get_file(key) as f:
            headers = header_parser.parse(f)
        if not _is_dmarc_subject(headers):
            continue
        # Pass 2: full message — only reached for DMARC emails
        msg = mdir[key]
        yield message_id(msg), msg


def scan_mbox(path):
    """Yield (msg_id, full_message) for mbox messages with DMARC-like subjects."""
    header_parser = email.parser.BytesHeaderParser(policy=email.policy.default)

    def _factory(f):
        return email.message_from_binary_file(f, policy=email.policy.default)

    mbox = mailbox.mbox(path, factory=_factory, create=False)
    for key in mbox.keys():
        with mbox.get_file(key) as f:
            headers = header_parser.parse(f)
        if not _is_dmarc_subject(headers):
            continue
        msg = mbox[key]
        yield message_id(msg), msg


def scan_mail(path):
    p = Path(path)
    if p.is_dir():
        # Maildir has cur/ new/ tmp/ subdirs
        if (p / "cur").exists() or (p / "new").exists():
            yield from scan_maildir(path)
        else:
            # Maybe a directory full of individual message files
            for f in sorted(p.iterdir()):
                if f.is_file():
                    try:
                        with open(f, "rb") as fh:
                            msg = email.message_from_binary_file(fh, policy=email.policy.default)
                        yield message_id(msg), msg
                    except Exception:
                        pass
    elif p.is_file():
        yield from scan_mbox(path)
    else:
        print(f"[error] Mail path not found: {path}", file=sys.stderr)
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────

def load_state(state_path):
    try:
        with open(state_path) as f:
            return set(json.load(f).get("seen", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_state(state_path, seen):
    with open(state_path, "w") as f:
        json.dump({"seen": list(seen)}, f)


def main():
    parser = argparse.ArgumentParser(description="DMARC mail scanner")
    parser.add_argument("--maildir", required=True, help="Path to Maildir or mbox file")
    parser.add_argument("--output",  required=True, help="Path to write reports.json")
    parser.add_argument("--state",   default=None,  help="Path to seen-IDs state file (optional, enables incremental mode)")
    parser.add_argument("--max-reports", type=int, default=200, help="Max reports to keep in output (default: 200)")
    args = parser.parse_args()

    seen     = load_state(args.state) if args.state else set()
    new_seen = set()
    reports  = []

    # Load existing reports if output exists (for incremental merge)
    out_path = Path(args.output)
    existing = []
    if out_path.exists() and args.state:
        try:
            with open(out_path) as f:
                existing = json.load(f).get("reports", [])
        except Exception:
            existing = []

    print(f"Scanning {args.maildir} ...", file=sys.stderr)
    new_count = 0

    for mid, msg in scan_mail(args.maildir):
        if mid in seen:
            continue

        new_seen.add(mid)

        extracted = extract_reports_from_message(msg)
        for report in extracted:
            # Add the email date as a fallback if report has no date
            if not report.get("date_end"):
                report["date_end"] = msg.get("Date", "")
            reports.append(report)
            new_count += 1
            print(f"  + {report['submitter']} → {report['domain']} ({report['date_end']}, {report['pass_rate']}% pass)", file=sys.stderr)

    # Merge with existing, deduplicate by report_id, sort newest first
    all_reports = reports + existing
    seen_ids = set()
    deduped  = []
    for r in all_reports:
        rid = r.get("report_id") or r.get("begin_ts", "") + r.get("submitter", "")
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            deduped.append(r)

    deduped.sort(key=lambda r: r.get("end_ts", "0"), reverse=True)
    deduped = deduped[:args.max_reports]

    output = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total_reports": len(deduped),
        "reports": deduped,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    if args.state:
        save_state(args.state, seen | new_seen)

    print(f"Done. {new_count} new report(s). {len(deduped)} total in {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
