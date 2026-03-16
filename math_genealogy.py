#!/usr/bin/env python3
"""
Math Genealogy Tree Generator using Geneagrapher
Fetches advisor-student chains from the Mathematics Genealogy Project
and renders them as a DOT graph (with optional PNG/SVG rendering via Graphviz).

Requirements:
    pip install geneagrapher-core requests beautifulsoup4

Optional (for PNG/SVG rendering):
    sudo apt install graphviz   # Linux
    brew install graphviz       # macOS
    winget install graphviz     # Windows
"""

import argparse
import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def _require(package: str, import_name: str | None = None):
    """Import a package, printing a helpful message if missing."""
    import importlib
    name = import_name or package
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        print(f"[error] Required package '{package}' is not installed.")
        print(f"        Run:  pip install {package}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Name → MGP ID lookup  (scrapes the MGP search page)
# ---------------------------------------------------------------------------

def search_mgp(name: str) -> list[dict]:
    """
    Search the Mathematics Genealogy Project for *name*.
    Returns a list of dicts: {id, name, university, year}.
    """
    requests = _require("requests")
    bs4 = _require("beautifulsoup4", "bs4")
    from bs4 import BeautifulSoup

    url = "https://www.genealogy.math.ndsu.nodak.edu/query-prep.php"
    params = {
        "family_name": "",
        "given_name": "",
        "other_names": "",
        "chrono": "0",
        "submit": "Submit",
    }

    # Try splitting the name into given / family parts
    parts = name.strip().split()
    if len(parts) >= 2:
        params["given_name"] = " ".join(parts[:-1])
        params["family_name"] = parts[-1]
    else:
        params["family_name"] = name

    resp = requests.post(url, data=params, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    # MGP search results are in a table with rows like:
    # <a href="id.php?id=12345">Name</a>  University  Year
    for a_tag in soup.select("table tr td a[href*='id.php?id=']"):
        href = a_tag["href"]
        mgp_id = int(href.split("id=")[-1])
        cells = a_tag.find_parent("tr").find_all("td")
        uni  = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        year = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        results.append({"id": mgp_id, "name": a_tag.get_text(strip=True),
                         "university": uni, "year": year})

    return results


# ---------------------------------------------------------------------------
# Build the graph with geneagrapher-core
# ---------------------------------------------------------------------------

async def build_graph(mgp_id: int, direction: str, max_depth: int):
    """
    Use geneagrapher-core to fetch ancestors or descendants from MGP.

    direction : "ancestors"   – follow advisor chain upward
                "descendants" – follow student chain downward
                "both"        – both directions
    """
    gc = _require("geneagrapher_core", "geneagrapher_core")

    from geneagrapher_core.traverse import build_graph as _build, TraverseItem, TraverseDirection
    from geneagrapher_core.record import RecordId

    flags = TraverseDirection(0)
    if direction in ("ancestors", "both"):
        flags |= TraverseDirection.ADVISORS
    if direction in ("descendants", "both"):
        flags |= TraverseDirection.DESCENDANTS

    print(f"[info] Fetching data from Mathematics Genealogy Project (id={mgp_id}) …")

    graph = await _build(
        [TraverseItem(RecordId(mgp_id), flags)],
        max_records=max_depth,
    )
    return graph


# ---------------------------------------------------------------------------
# Wikipedia Lookup
# ---------------------------------------------------------------------------

async def fetch_wikipedia_url(name: str) -> str:
    """Query the Wikipedia API for a page matching the mathematician's name."""
    import aiohttp
    import urllib.parse
    
    # Strip titles, weird spaces, or things that confuse Wikipedia
    clean_name = name.split(',')[0] if ',' in name else name  # MGP sometimes uses "Last, First"
    query = urllib.parse.quote(clean_name)
    url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={query}&limit=1&namespace=0&format=json"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as response:
                if response.status == 200:
                    data = await response.json()
                    # data format: [search_term, [titles], [descriptions], [urls]]
                    if len(data) >= 4 and len(data[3]) > 0:
                        return data[3][0]
    except Exception:
        pass
    
    return ""


# ---------------------------------------------------------------------------
# DOT output
# ---------------------------------------------------------------------------

async def graph_to_dot(graph) -> str:
    """Convert a geneagrapher Graph object to a DOT string, injecting Wikipedia links."""
    lines = [
        "digraph genealogy {",
        '    graph [bgcolor=transparent, rankdir=TB, ranksep=0.8, nodesep=0.5, splines=polyline, fontname="Helvetica,Arial,sans-serif"];',
        '    node [shape=rect, style="rounded,filled", fillcolor="#f8f9fa", fontname="Helvetica,Arial,sans-serif", color="#dee2e6", penwidth=2, margin="0.2,0.1"];',
        '    edge [color="#6c757d", penwidth=1.5, arrowhead=vee];'
    ]
    
    print(f"[info] Discovering Wikipedia links for {len(graph['nodes'])} mathematicians...")
    
    import asyncio
    
    async def process_node(node_id, r):
        name = r["name"].replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
        details = []
        if r.get("year"):
            details.append(str(r["year"]))
        if r.get("institution"):
            details.append(r["institution"].replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;'))
            
        wiki_url = await fetch_wikipedia_url(r["name"])
        
        if details:
            details_str = ", ".join(details)
            label = f'<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0"><TR><TD><B>{name}</B></TD></TR><TR><TD><FONT POINT-SIZE="10" COLOR="#666666">{details_str}</FONT></TD></TR></TABLE>>'
        else:
            label = f'<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0"><TR><TD><B>{name}</B></TD></TR></TABLE>>'
            
        url_attr = f', URL="{wiki_url}", target="_blank"' if wiki_url else ""
        return f'    {node_id} [label={label}{url_attr}];'
        
    tasks = [process_node(node_id, r) for node_id, r in graph["nodes"].items()]
    node_lines = await asyncio.gather(*tasks)
    lines.extend(node_lines)
    
    for node_id, r in graph["nodes"].items():
        for advisor_id in r.get("advisors", []):
            lines.append(f"    {advisor_id} -> {node_id};")
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Render DOT → image via Graphviz CLI
# ---------------------------------------------------------------------------

def render_dot(dot_text: str, output_path: Path, fmt: str = "png") -> bool:
    """Render *dot_text* to *output_path* using the `dot` binary."""
    try:
        result = subprocess.run(
            ["dot", f"-T{fmt}", "-o", str(output_path)],
            input=dot_text.encode(),
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f"[warn] Graphviz error: {result.stderr.decode().strip()}")
            return False
        return True
    except FileNotFoundError:
        print("[warn] `dot` (Graphviz) not found. Skipping image render.")
        print("       Install Graphviz to generate PNG/SVG output.")
        return False


# ---------------------------------------------------------------------------
# Render Interactive HTML via d3-graphviz
# ---------------------------------------------------------------------------

def render_html(dot_text: str, output_path: Path):
    """Generate an interactive HTML file to view the graph."""
    import base64
    b64_dot = base64.b64encode(dot_text.encode('utf-8')).decode('utf-8')
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Interactive Math Genealogy Tree</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <script src="https://unpkg.com/@hpcc-js/wasm@2.20.0/dist/graphviz.umd.js"></script>
    <script src="https://unpkg.com/d3-graphviz@5.4.0/build/d3-graphviz.js"></script>
    <style>
        body {{ margin: 0; padding: 0; overflow: hidden; font-family: sans-serif; background-color: #f8f9fa; }}
        #graph {{ width: 100vw; height: 100vh; text-align: center; }}
        #controls {{ position: absolute; top: 20px; left: 20px; background: rgba(255,255,255,0.95); padding: 15px 20px; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); border: 1px solid #dee2e6; z-index: 10; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }}
        h3 {{ margin: 0 0 12px 0; color: #343a40; font-size: 18px; }}
        .instruction {{ display: flex; align-items: center; margin: 8px 0; color: #495057; font-size: 14px; }}
        .icon {{ font-size: 18px; margin-right: 10px; width: 24px; text-align: center; }}
    </style>
</head>
<body>
    <div id="controls">
        <h3>Math Genealogy Tree</h3>
        <div class="instruction"><span class="icon">🖱️</span> Scroll wheel to zoom</div>
        <div class="instruction"><span class="icon">✋</span> Click & drag to pan</div>
        <div class="instruction"><span class="icon">🔍</span> Hover on nodes to inspect</div>
    </div>
    <div id="graph"></div>
    <script>
        const b64 = "{b64_dot}";
        const binaryString = window.atob(b64);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {{
            bytes[i] = binaryString.charCodeAt(i);
        }}
        const dotSrc = new TextDecoder('utf-8').decode(bytes);
        
        var graphviz = d3.select("#graph").graphviz()
            .width(window.innerWidth)
            .height(window.innerHeight)
            .fit(true)
            .renderDot(dotSrc);
            
        window.addEventListener('resize', () => {{
            graphviz
                .width(window.innerWidth)
                .height(window.innerHeight)
                .fit(true)
                .renderDot(dotSrc);
        }});
    </script>
</body>
</html>"""
    output_path.write_text(html_content, encoding="utf-8")

# ---------------------------------------------------------------------------
# Interactive name selection
# ---------------------------------------------------------------------------

def pick_mathematician(name: str) -> int:
    """Search MGP and let the user pick the right person."""
    print(f"[info] Searching Mathematics Genealogy Project for '{name}' …")
    results = search_mgp(name)

    if not results:
        print(f"[error] No results found for '{name}'.")
        print("        Try a different spelling or visit https://genealogy.math.ndsu.nodak.edu/")
        sys.exit(1)

    if len(results) == 1:
        r = results[0]
        print(f"[info] Found: {r['name']}  ({r['university']}, {r['year']})  id={r['id']}")
        return r["id"]

    print(f"\nFound {len(results)} matches:\n")
    for i, r in enumerate(results, 1):
        print(f"  [{i}] {r['name']:<35} {r['university']:<35} {r['year']}")

    while True:
        raw = input("\nEnter number to select (or 0 to quit): ").strip()
        if raw == "0":
            sys.exit(0)
        if raw.isdigit() and 1 <= int(raw) <= len(results):
            return results[int(raw) - 1]["id"]
        print("    Invalid choice, try again.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a math genealogy tree from the Mathematics Genealogy Project.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples
            --------
            # Ancestor chain for Leonhard Euler (by name)
            python math_genealogy.py "Leonhard Euler"

            # Use a known MGP id directly, get descendants
            python math_genealogy.py --id 17864 --direction descendants

            # Both directions, save PNG
            python math_genealogy.py "Carl Friedrich Gauss" --direction both --format png
        """),
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("name", nargs="?", metavar="NAME",
                     help="Mathematician name to look up on MGP")
    grp.add_argument("--id", type=int, dest="mgp_id",
                     help="MGP record id (skips name search)")

    parser.add_argument("--direction", choices=["ancestors", "descendants", "both"],
                        default="ancestors",
                        help="Which chain to follow (default: ancestors)")
    parser.add_argument("--output", "-o", default="genealogy",
                        help="Output file base name, without extension (default: genealogy)")
    parser.add_argument("--format", "-f", choices=["dot", "png", "svg", "pdf", "html"],
                        default="dot",
                        help="Output format (default: dot). PNG/SVG/PDF require Graphviz. HTML produces an interactive d3-graphviz viewer.")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    args = parse_args()

    # Resolve MGP id
    if args.mgp_id:
        mgp_id = args.mgp_id
    else:
        mgp_id = pick_mathematician(args.name)

    # Build graph
    graph = await build_graph(mgp_id, args.direction, max_depth=99)

    # Produce DOT
    dot_text = await graph_to_dot(graph)

    dot_path = Path(args.output).with_suffix(".dot")
    dot_path.write_text(dot_text, encoding="utf-8")
    print(f"[ok]   DOT file written → {dot_path}")

    # Optionally render image
    if args.format == "html":
        html_path = Path(args.output).with_suffix(".html")
        render_html(dot_text, html_path)
        print(f"[ok]   Interactive HTML written → {html_path}")
    elif args.format != "dot":
        img_path = Path(args.output).with_suffix(f".{args.format}")
        ok = render_dot(dot_text, img_path, args.format)
        if ok:
            print(f"[ok]   Image written    → {img_path}")

    # Print summary
    node_count = len(graph["nodes"]) if "nodes" in graph else "?"
    print(f"\nSummary: {node_count} nodes in the {args.direction} graph.")
    print("Open the .dot file with Graphviz or https://dreampuf.github.io/GraphvizOnline/")


if __name__ == "__main__":
    asyncio.run(main())
