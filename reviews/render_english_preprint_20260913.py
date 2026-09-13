"""Render only the dated revised drafts; preserve the historical renderer/output."""
from pathlib import Path
import re
import subprocess
from render_english_preprint_20260910 import convert

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper-build-20260913"


def main():
    OUT.mkdir(exist_ok=True)
    for stem in ("paper-manuscript-en-20260913", "paper-supplement-en-20260913"):
        tex = OUT / (stem + ".tex")
        source = (ROOT / (stem + ".md")).read_text()
        # Only standalone $$...$$ blocks are math; historical converter stays unchanged.
        equations = []
        def capture(match):
            equations.append(match.group(1).strip())
            return 'PAPERDISPLAYMATH' + str(len(equations) - 1) + 'END'
        source = re.sub(r'^\$\$\n(.*?)\n\$\$$', capture, source, flags=re.M | re.S)
        rendered = convert(source, "supplement" in stem)
        rendered = rendered.replace(r'\usepackage{array,longtable}', r'\usepackage{array,longtable,amsmath}')
        rendered = rendered.replace(r'{\Large\bfseries ', r'{\raggedright\hyphenpenalty=10000\Large\bfseries ', 1)
        for i, equation in enumerate(equations):
            rendered = rendered.replace('PAPERDISPLAYMATH' + str(i) + 'END', '\\[\n' + equation + '\n\\]')
        rendered = rendered.replace("10 September 2026", "13 September 2026")
        tex.write_text(rendered)
        for _ in range(2):
            result = subprocess.run(
                ["xelatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", tex.name],
                cwd=OUT, capture_output=True, text=True,
            )
            if result.returncode:
                raise RuntimeError(result.stdout[-5000:] + result.stderr[-1000:])
        log = tex.with_suffix(".log").read_text(errors="replace")
        issues = [line for line in log.splitlines() if any(
            key in line for key in ("Overfull", "Missing character", "Undefined control")
        )]
        print(stem, re.findall(r"Output written on .*", log), "layout issues:", issues)
        assert not issues, issues


if __name__ == "__main__":
    main()
