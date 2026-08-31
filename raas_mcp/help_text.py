"""Build user-facing help strings from RaaS RPC metadata (bundled schema)."""

from __future__ import annotations

import re
from typing import Any


_LOADEDMOD_BOILERPLATE = "the loadedmod class allows for the module loaded onto the sub"


def is_generic_resource_doc(doc: str) -> bool:
    """True when RaaS reused Salt's generic LoadedMod docstring."""
    d = (doc or "").strip().lower()
    if not d:
        return True
    return _LOADEDMOD_BOILERPLATE in d[:400]


def first_line(text: str, max_len: int = 88) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    line = text.splitlines()[0].strip()
    if len(line) <= max_len:
        return line
    return line[: max_len - 3].rstrip() + "..."


def _indent_block(text: str, prefix: str = "  ") -> str:
    out = []
    for line in (text or "").splitlines():
        out.append(prefix + line if line.strip() else "")
    return "\n".join(out).rstrip()


def param_help_from_rpc_doc(doc: str, pname: str) -> str | None:
    """Extract description for :pname: from RPC doc (line-based or packed on one line)."""
    if not doc or not pname:
        return None
    marker = f":{pname}:"
    for raw in doc.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith(marker.lower()):
            rest = line[len(marker) :].lstrip()
            return rest or None

    low = doc.lower()
    mlow = marker.lower()
    idx = low.find(mlow)
    if idx < 0:
        return None
    rest = doc[idx + len(marker) :].lstrip()
    nxt = re.search(r"(?i)\s:[a-z0-9_]+:", rest)
    if nxt:
        rest = rest[: nxt.start()]
    return rest.strip() or None


def prettify_packed_param_doc(doc: str) -> str:
    """Break inline ':name: description' fragments onto separate lines for readability."""
    if not doc:
        return doc
    return re.sub(r" (\:[a-z0-9_]+\:)", r"\n\1", doc, flags=re.IGNORECASE)


def _normalize_returns(text: str) -> str | None:
    t = (text or "").strip()
    if not t or t in ("None", "<class 'NoneType'>"):
        return None
    if t.startswith("<class '") and t.endswith("'>"):
        return t[len("<class '") : -2]
    return t


def build_rpc_command_help(resource: str, method: str, minfo: dict[str, Any], *, max_chars: int = 16000) -> tuple[str, str]:
    """
    Return (full_help, short_help) for a typed RPC subcommand.

    `short_help` is used in parent command listings; `full_help` is shown for --help.
    """
    detailed = minfo.get("detailed") or {}
    doc_full = (detailed.get("doc") or "").strip()
    if not doc_full and isinstance(minfo.get("formatted"), str):
        doc_full = str(minfo["formatted"]).strip()

    sig = (detailed.get("signature") or "").strip()
    ret = detailed.get("returns")
    ret_s = _normalize_returns(str(ret)) if ret not in (None, "") else None

    title = f"RaaS — {resource}.{method}"
    parts: list[str] = [title, ""]

    if sig:
        parts.append("Signature")
        parts.append("")
        parts.append(_indent_block(sig))
        parts.append("")

    if doc_full:
        doc_fmt = doc_full.strip()
        # Help the terminal formatter: ensure each :param: line starts its own paragraph.
        doc_fmt = re.sub(r"\n(:[a-z0-9_]+:)", r"\n\n\1", doc_fmt, flags=re.IGNORECASE)
        parts.append(prettify_packed_param_doc(doc_fmt))

    if ret_s:
        parts.append("")
        parts.append("Returns")
        parts.append("")
        parts.append(_indent_block(ret_s))

    text = "\n".join(parts).strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."

    short_src = doc_full or sig or title
    short = first_line(short_src, max_len=90) or f"{resource}.{method}"
    return text, short


def resource_help_from_body(resource: str, body: dict[str, Any], *, max_chars: int = 12000) -> tuple[str, str]:
    """Return (full_help, short_help) for a typed RPC command group."""
    raw_doc = (body.get("__doc__") or "").strip()
    methods = sorted(
        m for m in body if m != "__doc__" and isinstance(body.get(m), dict)
    )
    n = len(methods)
    title = f"Command group `{resource}`"

    if not raw_doc or is_generic_resource_doc(raw_doc):
        preview = ", ".join(m.replace("_", "-") for m in methods[:10])
        if n > 10:
            preview += f", ... (+{n - 10} more)"
        count_lbl = "1 subcommand" if n == 1 else f"{n} subcommands" if n else "no subcommands"
        body_text = (
            f"RaaS command group `{resource}` ({count_lbl}).\n\n"
            "Discovery often repeats a generic Salt LoadedMod line for every resource; "
            "this summary replaces it with a quick index.\n\n"
            f"Subcommands: {preview or '(none)'}\n\n"
            f"Try  vcf-salt {resource} <subcommand> --help  for full docs from the server."
        )
        if n == 1:
            short = f"{resource}: 1 subcommand"
        elif n:
            short = f"{resource}: {n} subcommands"
        else:
            short = f"{resource}: (empty)"
        if len(short) > 72:
            short = short[:69] + "..."
    else:
        body_text = raw_doc
        short = first_line(raw_doc, max_len=72) or f"RaaS resource {resource}"

    text = f"{title}\n\n{body_text}".strip()
    if len(text) > max_chars:
        text = text[: max_chars - 3].rstrip() + "..."
    return text, short
