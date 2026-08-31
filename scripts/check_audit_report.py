#!/usr/bin/env python3
"""Evaluate a pip-audit JSON report: fail only on vulnerabilities with no
available fix; report (without failing) on vulnerabilities that do have a
fix available, so PRs surface them without blocking on transitive findings
that simply haven't been bumped yet.

Usage: python scripts/check_audit_report.py <path-to-pip-audit-json>
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_audit_report.py <audit.json>", file=sys.stderr)
        return 2

    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)

    deps = data["dependencies"] if isinstance(data, dict) and "dependencies" in data else data

    fixed: list[str] = []
    unfixed: list[str] = []
    for dep in deps:
        for vuln in dep.get("vulns", []):
            entry = f"{dep['name']} {dep['version']}: {vuln['id']}"
            if vuln.get("fix_versions"):
                fixed.append(f"{entry} (fix available: {', '.join(vuln['fix_versions'])})")
            else:
                unfixed.append(f"{entry} (no fix available)")

    if fixed:
        print("Vulnerabilities with an available fix (upgrade recommended, not blocking):")
        for line in fixed:
            print(" -", line)

    if unfixed:
        print("Vulnerabilities with NO available fix (blocking):")
        for line in unfixed:
            print(" -", line)
        return 1

    print(f"pip-audit: {len(fixed)} fixable finding(s) reported, 0 blocking finding(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
