#!/usr/bin/env python3
"""Genera el HTML imprimible de la guÃ­a educativa (luego se convierte a PDF)."""

from __future__ import annotations

import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "GUIA-EDUCATIVA.md"
TARGET = ROOT / "docs" / "guia-educativa.html"

md_text = SOURCE.read_text(encoding="utf-8")

# La portada ya incluye tÃ­tulo y autor: se quita el bloque inicial del Markdown.
md_text = re.sub(r"\A# .*?\n\n.*?\n\n---\n\n", "", md_text, count=1, flags=re.S)

body = markdown.markdown(
    md_text,
    extensions=["toc", "tables", "fenced_code", "sane_lists"],
    extension_configs={"toc": {"toc_depth": "1-2"}},
)

HTML = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Nexus-Core â€” Guía educativa y técnica</title>
<style>
  @page {{
    size: A4;
    margin: 17mm 15mm 18mm 15mm;
  }}

  :root {{
    --ink: #1f2933;
    --muted: #5f6b76;
    --rule: #d9d2c7;
    --accent: #0b3954;
    --accent-2: #b5543a;
    --paper: #fffdf9;
    --soft: #f5f1e9;
  }}

  * {{ box-sizing: border-box; }}

  html, body {{
    margin: 0;
    padding: 0;
    background: #fff;
    color: var(--ink);
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
    font-size: 10.5pt;
    line-height: 1.55;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}

  .cover {{
    height: 257mm;
    margin: 0;
    padding: 26mm 22mm;
    background: linear-gradient(145deg, #071f31 0%, #0b3954 62%, #12506f 100%);
    color: #fff;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    page-break-after: always;
  }}

  .cover__kicker {{
    margin: 0;
    font-size: 10pt;
    letter-spacing: 0.22em;
    text-transform: uppercase;
    color: #c9b8a0;
  }}

  .cover h1 {{
    margin: 10mm 0 0;
    font-size: 42pt;
    line-height: 1.02;
    letter-spacing: -0.03em;
    color: #fff;
  }}

  .cover__sub {{
    margin: 4mm 0 0;
    font-size: 17pt;
    color: #f1e9dc;
  }}

  .cover__desc {{
    max-width: 125mm;
    margin: 7mm 0 0;
    font-size: 11.5pt;
    line-height: 1.55;
    color: #cfe0e8;
  }}

  .cover__meta {{
    font-size: 10pt;
    color: #c9b8a0;
    border-top: 1px solid rgba(255,255,255,.22);
    padding-top: 5mm;
  }}

  .cover__bar {{
    width: 28mm;
    height: 3px;
    margin-top: 7mm;
    background: var(--accent-2);
  }}

  .content {{
    padding: 0 2mm;
  }}

  .cover h1 {{ page-break-before: avoid; break-before: avoid; }}

  h1 {{
    page-break-before: always;
    margin: 0 0 4mm;
    padding-bottom: 2.5mm;
    font-size: 22pt;
    line-height: 1.15;
    letter-spacing: -0.02em;
    color: var(--accent);
    border-bottom: 2px solid var(--accent-2);
  }}

  h2 {{
    margin: 7mm 0 2.5mm;
    font-size: 14pt;
    line-height: 1.25;
    color: var(--accent);
    page-break-after: avoid;
  }}

  h3 {{
    margin: 5mm 0 2mm;
    font-size: 11.5pt;
    color: #243746;
    page-break-after: avoid;
  }}

  p {{ margin: 0 0 3mm; }}

  a {{ color: #0b5f86; text-decoration: none; }}

  strong {{ color: #102a3a; }}

  em {{ color: #3d4c57; }}

  ul, ol {{ margin: 0 0 3.5mm 5mm; padding: 0; }}
  li {{ margin-bottom: 1.2mm; }}

  blockquote {{
    margin: 4mm 0;
    padding: 3mm 4mm;
    background: var(--soft);
    border-left: 3px solid var(--accent-2);
    color: #33424d;
    page-break-inside: avoid;
  }}

  blockquote p {{ margin: 0; }}

  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 3.5mm 0 5mm;
    font-size: 9.3pt;
    page-break-inside: avoid;
  }}

  th {{
    background: var(--accent);
    color: #fff;
    text-align: left;
    padding: 2mm 2.5mm;
    font-weight: 600;
  }}

  td {{
    padding: 1.8mm 2.5mm;
    border-bottom: 0.6pt solid var(--rule);
    vertical-align: top;
  }}

  tr:nth-child(even) td {{ background: #fbf8f2; }}

  code {{
    font-family: "Consolas", "Cascadia Mono", monospace;
    font-size: 9pt;
    background: #f1ece3;
    padding: 0.4mm 1.1mm;
    border-radius: 2px;
  }}

  pre {{
    margin: 3mm 0 4mm;
    padding: 3mm 3.5mm;
    background: #f6f2eb;
    border: 0.6pt solid var(--rule);
    border-left: 3px solid var(--accent);
    border-radius: 2px;
    white-space: pre-wrap;
    word-break: break-word;
    page-break-inside: avoid;
  }}

  pre code {{
    background: transparent;
    padding: 0;
    font-size: 8.6pt;
    line-height: 1.45;
  }}

  .toc {{
    margin: 2mm 0 6mm;
    padding: 4mm 5mm;
    background: var(--soft);
    border: 0.6pt solid var(--rule);
    page-break-after: always;
  }}

  .toc > ul {{ margin: 0 0 0 4mm; }}
  .toc ul {{ margin: 0 0 0 4mm; }}
  .toc li {{ margin-bottom: 1mm; }}
  .toc a {{ color: #243746; }}

  hr {{
    border: 0;
    border-top: 0.6pt solid var(--rule);
    margin: 6mm 0;
  }}

  .content > h1:first-of-type {{ page-break-before: auto; }}

  @media print {{
    .cover {{ break-after: page; }}
  }}
</style>
</head>
<body>
  <section class="cover">
    <div>
      <p class="cover__kicker">Trabajo Fin de Grado · SMR</p>
      <h1>Nexus-Core</h1>
      <div class="cover__bar"></div>
      <p class="cover__sub">Guía educativa y técnica</p>
      <p class="cover__desc">
        Monitorización, administración y seguridad de infraestructura con agentes remotos.
        Arquitectura, datos, redes, seguridad y laboratorios para clase.
      </p>
    </div>
    <div class="cover__meta">
      Saúl Moreno · Málaga · Octubre de 2026<br>
      Documento educativo · Versión 1.0
    </div>
  </section>

  <main class="content">
    {body}
  </main>
</body>
</html>
"""

TARGET.write_text(HTML, encoding="utf-8")
print(f"HTML: {TARGET} ({len(HTML) // 1024} KB)")

