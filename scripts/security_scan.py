from __future__ import annotations
import json
import re
from pathlib import Path

patterns = {
    "hardcoded_secret": re.compile(r"(?i)(password|secret|api[_-]?key)\s*=\s*['\"][^'\"]{8,}['\"]"),
    "unsafe_eval": re.compile(r"\beval\s*\("),
    "unsafe_exec": re.compile(r"\bexec\s*\("),
    "shell_true": re.compile(r"subprocess\.(run|Popen)\([^\n]*shell\s*=\s*True"),
}
findings = []
for root in (Path("app"), Path("scripts")):
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".js", ".html"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for code, pattern in patterns.items():
            for match in pattern.finditer(text):
                snippet = text[max(0, match.start()-60):match.end()+80].replace("\n", " ")
                if "change-this-to-a-long-random-secret" in snippet or "test-secret" in snippet:
                    continue
                findings.append({"severity": "HIGH" if code in {"hardcoded_secret", "unsafe_eval", "unsafe_exec"} else "MEDIUM", "code": code, "file": str(path), "snippet": snippet[:220]})
report = {"status": "PASS" if not findings else "FAIL", "high": sum(f["severity"] == "HIGH" for f in findings), "medium": sum(f["severity"] == "MEDIUM" for f in findings), "findings": findings}
path = Path("release_artifacts/static-security-scan.json")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
raise SystemExit(0 if not findings else 1)
