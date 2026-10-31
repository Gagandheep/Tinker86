#!/usr/bin/env python3
"""Render docs/USER_GUIDE.md into docs/USER_GUIDE.pdf.

    python tools/build_guide_pdf.py

Needs pandoc (Markdown to HTML) and Playwright with Chromium (HTML to PDF):
pip install playwright && playwright install chromium. Commit the PDF with the
Markdown, so the release build only copies both (tools/package.py).
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')
SOURCE = os.path.join(DOCS, 'USER_GUIDE.md')
TARGET = os.path.join(DOCS, 'USER_GUIDE.pdf')

STYLE = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
body { font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif; font-size: 10.5pt;
       line-height: 1.45; color: #1d1d1f; max-width: none; margin: 0; padding: 0; }
h1 { font-size: 24pt; margin: 0 0 6pt; color: #12355b; }
h2 { font-size: 16pt; margin-top: 22pt; padding-bottom: 3pt; border-bottom: 1.5pt solid #12355b;
     color: #12355b; break-after: avoid; }
h2:not(:first-of-type) { break-before: page; }
h3 { font-size: 12pt; margin-top: 14pt; color: #12355b; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
a { color: #1a5fb4; text-decoration: none; }
code { font-family: "DejaVu Sans Mono", Consolas, Menlo, monospace; font-size: 9pt;
       background: #f1f3f6; padding: 0.5pt 2.5pt; border-radius: 2pt; }
pre { background: #f1f3f6; border: 0.5pt solid #d8dde4; border-radius: 3pt; padding: 6pt 8pt;
      font-size: 8.8pt; line-height: 1.35; white-space: pre-wrap; break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.5pt; break-inside: avoid; }
th, td { border: 0.5pt solid #c9d0d9; padding: 3pt 6pt; text-align: left; vertical-align: top; }
th { background: #e8eef5; }
img { max-width: 100%; border: 0.5pt solid #c9d0d9; display: block; margin: 6pt auto; break-inside: avoid; }
figure { margin: 8pt 0; break-inside: avoid; }
figcaption { font-size: 9pt; color: #5a6270; text-align: center; }
hr { display: none; }
"""


def main():
    with tempfile.TemporaryDirectory() as directory:
        html = os.path.join(directory, 'guide.html')
        css = os.path.join(directory, 'guide.css')
        with open(css, 'w', encoding='utf-8') as f:
            f.write(STYLE)
        # gfm_auto_identifiers: the same anchors as on GitHub, so the contents links work
        subprocess.run(['pandoc', SOURCE, '-f', 'markdown+gfm_auto_identifiers-implicit_figures', '-t', 'html5',
                        '--standalone', '--no-highlight', '--embed-resources', '--resource-path', DOCS, '--css', css,
                        '--metadata', 'pagetitle=8086 Simulator User Guide', '-o', html], check=True)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            # CHROMIUM: use an existing Chrome/Chromium instead of Playwright's own download
            browser = playwright.chromium.launch(executable_path=os.environ.get('CHROMIUM') or None)
            page = browser.new_page()
            page.goto('file://' + html)
            page.pdf(path=TARGET, format='A4', print_background=True, display_header_footer=True,
                     header_template='<span></span>',
                     footer_template='<div style="font-size:8pt;color:#777;width:100%;text-align:center">'
                                     '8086 Simulator User Guide &middot; <span class="pageNumber"></span></div>',
                     margin={'top': '18mm', 'bottom': '20mm', 'left': '16mm', 'right': '16mm'})
            browser.close()
    print('wrote', os.path.relpath(TARGET, ROOT))


if __name__ == '__main__':
    sys.exit(main())
