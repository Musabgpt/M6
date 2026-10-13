"""Risk labels for shell commands, ported from NewAl's SafetyPolicy.java.

Every command still needs the user's approval. This only explains *why* a command is risky,
so the approval card can warn the user before they tap Allow.
"""

from __future__ import annotations

import re

_RULES = [
    (r"(?:^|[\s;&|(])(?:sudo|su|tsu)\s", "تشغيل بصلاحيات الجذر / runs as root"),
    (r"\brm\s+(?:-\S+\s+)*(?:/|~|\$HOME|\$PREFIX|/sdcard|/storage)(?:[\s/'\"]|$)",
     "حذف ملفات خارج المشروع / deletes files outside the project"),
    (r"\brm\s+(?:-\S+\s+)*(?:\.\.|\*)(?:[\s/'\"]|$)", "حذف جماعي بنمط عام / broad wildcard delete"),
    (r"\b(?:mkfs|fdisk|parted)\b|\bdd\s+[^\n]*\bof=", "كتابة مباشرة على أقراص / raw disk write"),
    (r">\s*/dev/(?:sd|block|mmc)", "كتابة مباشرة على أقراص / raw disk write"),
    (r"\b(?:reboot|shutdown|poweroff)\b", "إيقاف أو إعادة تشغيل الجهاز / power off or reboot"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\bch(?:mod|own)\s+-R\s+\S+\s+(?:/|~|\$HOME)",
     "تغيير صلاحيات خارج المشروع / changes permissions outside the project"),
    (r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:ba|z)?sh\b",
     "تشغيل سكربت من الإنترنت مباشرة / pipes a download into a shell"),
    (r"\b(?:pkg|apt|apt-get)\s+(?:install|uninstall|remove|purge|upgrade|autoremove)\b",
     "تعديل حزم النظام / changes system packages"),
    (r"\bgit\s+(?:push\s+(?:-f|--force)|reset\s+--hard|clean\s+-\S*f)",
     "عملية git لا يمكن التراجع عنها / irreversible git operation"),
]
_PATTERNS = [(re.compile(pattern, re.MULTILINE), reason) for pattern, reason in _RULES]


def risk_reason(command: str) -> str | None:
    """Return a short reason when the command is risky, or None when it looks ordinary."""
    if not isinstance(command, str):
        return None
    for pattern, reason in _PATTERNS:
        match = pattern.search(command)
        if match:
            return f"{reason}: {match.group().strip()}"
    return None
