"""Render the two English Markdown drafts using a neutral XeLaTeX layout.

Standard library only. This is a deliberately small converter for the markup
used in these manuscripts, not a general Markdown implementation. Generated
files go only to paper-build-20260910; source manuscripts are never edited.
"""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "paper-build-20260910"


def escape(text):
    table = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%",
             "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{",
             "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(table.get(c, c) for c in text.replace(r"\{", "{").replace(r"\}", "}"))


def inline(text):
    pattern = r"\[([^\]]+)\]\(([^)]+)\)|`([^`]+)`"
    parts, start = [], 0
    for m in re.finditer(pattern, text):
        parts.append(escape(text[start:m.start()]))
        if m.group(3) is not None:
            parts.append(escape(m.group(3)))
        elif m.group(2).startswith("https://"):
            parts.append(r"\href{" + m.group(2).replace("%", r"\%") + "}{" + escape(m.group(1)) + "}")
        else:
            # Local repository links remain in Markdown, not broken PDF URLs.
            parts.append(escape(m.group(1)))
        start = m.end()
    return "".join(parts) + escape(text[start:])


def convert(source, supplement=False):
    lines = source.splitlines()
    title = lines[0].removeprefix("# ")
    body, i, in_list = [], 1, False
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        # Editorial status is replaced by the PDF's explicitly neutral label.
        if line.startswith(("Working manuscript,", "Corresponding [main text]")):
            i += 1
            continue
        if in_list and not line.startswith("- "):
            body.append(r"\end{itemize}")
            in_list = False
        if line.startswith("## Supplement and Availability"):
            body.append(r"\section*{Supplement and Availability}")
            body.append("Supplementary methods and results accompany this draft. Repository links and publication-readiness notes remain in the Markdown sources. A public reproducibility package is not yet released.")
            break
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            n = len(rows[0])
            assert all(len(r) == n for r in rows)
            if n == 4:
                widths = [0.49, 0.15, 0.15, 0.15]
            else:
                widths = [0.27, 0.33, 0.34]
            spec = "".join(r">{\raggedright\arraybackslash}p{" + str(w) + r"\linewidth}" for w in widths)
            body.append(r"{\small\setlength{\tabcolsep}{3pt}\renewcommand{\arraystretch}{1.18}")
            header = " & ".join(r"\textbf{" + inline(c) + "}" for c in rows[0]) + r" \\ \hline"
            multipage = supplement and len(rows) > 20
            if multipage:
                body.append(r"\begin{longtable}{" + spec + "}")
                body.extend([r"\hline", header, r"\endfirsthead", r"\hline", header, r"\endhead"])
            else:
                body.append(r"\par\noindent\begin{minipage}{\linewidth}\begin{tabular}{" + spec + "}")
                body.extend([r"\hline", header])
            for row in rows[1:]:
                body.append(" & ".join(inline(c) for c in row) + r" \\")
            body.extend([r"\hline", r"\end{longtable}" if multipage else r"\end{tabular}\end{minipage}\par", "}"])
            continue
        if line.startswith("### "):
            body.append(r"\subsection*{" + inline(line[4:]) + "}")
        elif line.startswith("## "):
            heading = line[3:]
            if heading == "References":
                body.append(r"\label{main-end}\clearpage")
            body.append(r"\section*{" + inline(heading) + "}")
        elif line.startswith("- "):
            if not in_list:
                body.append(r"\begin{itemize}\setlength{\itemsep}{2pt}\setlength{\parsep}{0pt}")
                in_list = True
            body.append(r"\item " + inline(line[2:]))
        else:
            body.append(inline(line) + "\n")
        i += 1
    if in_list:
        body.append(r"\end{itemize}")
    preamble = r"""\documentclass[10pt,a4paper]{article}
\usepackage[margin=19mm]{geometry}
\usepackage{fontspec}
\setmainfont{DejaVu Serif}
\usepackage{array,longtable}
\usepackage[unicode,hidelinks]{hyperref}
\makeatletter
\renewcommand\section{\@startsection{section}{1}{0pt}{1.8ex plus .3ex}{.7ex}{\normalfont\large\bfseries}}
\renewcommand\subsection{\@startsection{subsection}{2}{0pt}{1.4ex plus .3ex}{.5ex}{\normalfont\normalsize\bfseries}}
\makeatother
\setlength{\parindent}{0pt}
\setlength{\parskip}{3pt}
\setlength{\emergencystretch}{3em}
\pagestyle{plain}
\begin{document}
"""
    return preamble + r"{\Large\bfseries " + inline(title) + r"\par}" + "\n" + r"\smallskip {\small Neutral preprint layout --- working draft, 10 September 2026.}\par" + "\n" + "\n".join(body) + "\n" + r"\end{document}" + "\n"


def main():
    OUT.mkdir(exist_ok=True)
    for stem in ("paper-manuscript-en-20260910", "paper-supplement-en-20260910"):
        tex = OUT / (stem + ".tex")
        tex.write_text(convert((ROOT / (stem + ".md")).read_text(), "supplement" in stem))
        for _ in range(2):
            result = subprocess.run(["xelatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", tex.name], cwd=OUT, capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stdout[-5000:])
        log = tex.with_suffix(".log").read_text(errors="replace")
        issues = [l for l in log.splitlines() if any(k in l for k in ("Overfull", "Missing character", "Undefined control"))]
        print(stem, re.findall(r"Output written on .*", log), "layout issues:", issues)


if __name__ == "__main__":
    main()
