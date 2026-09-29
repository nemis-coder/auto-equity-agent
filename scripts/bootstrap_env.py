"""Genera .env a partir de .env.example con secretos locales aleatorios.

Solo usa la biblioteca estándar para poder ejecutarse antes de instalar dependencias.
Nunca sobrescribe un .env existente.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

PLACEHOLDER = "__GENERATE__"
PROVIDER_HINT = (
    "La conversación con el cliente necesita un proveedor de IA. En .env, define\n"
    "CHAT_PROVIDER=anthropic y ANTHROPIC_API_KEY, o CHAT_PROVIDER=openai con\n"
    "OPENAI_API_KEY y CHAT_MODEL explícito. EXTRACTION_PROVIDER=fake permite pruebas\n"
    "sin costo; anthropic/openai leen documentos reales (OpenAI exige EXTRACTION_MODEL)."
)


def render(template: str) -> str:
    lines = []
    for line in template.splitlines():
        while PLACEHOLDER in line:
            line = line.replace(PLACEHOLDER, secrets.token_urlsafe(24), 1)
        lines.append(line)
    return "\n".join(lines) + "\n"


def bootstrap(example: Path, target: Path) -> bool:
    if target.exists():
        return False
    content = render(example.read_text(encoding="utf-8"))
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return True


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--example", type=Path, default=root / ".env.example")
    parser.add_argument("--target", type=Path, default=root / ".env")
    args = parser.parse_args(argv)
    if bootstrap(args.example, args.target):
        print(f"Creado {args.target} con secretos locales aleatorios.")
    else:
        print(f"{args.target} ya existe; no se modificó.")
    print(PROVIDER_HINT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
