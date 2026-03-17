import asyncio
import base64
import concurrent.futures
import streamlit as st
import streamlit.components.v1 as components
import math_genealogy

# Set page configuration
st.set_page_config(
    page_title="Math Genealogy Tree",
    page_icon="🌳",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Helper: run async functions safely (works even if an event loop is running)
# ---------------------------------------------------------------------------

def run_async(coro):
    """Run an async coroutine from a sync context, even inside Streamlit."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Streamlit already has a running loop – run in a thread pool
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Interactive D3-Graphviz HTML viewer
# ---------------------------------------------------------------------------

def get_viewer_html(dot_src: str) -> str:
    b64_dot = base64.b64encode(dot_src.encode("utf-8")).decode("utf-8")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <script src="https://unpkg.com/@hpcc-js/wasm@2.20.0/dist/graphviz.umd.js"></script>
    <script src="https://unpkg.com/d3-graphviz@5.4.0/build/d3-graphviz.js"></script>
    <style>
        body {{
            margin: 0;
            padding: 0;
            overflow: hidden;
            font-family: 'Segoe UI', Helvetica, sans-serif;
            background: transparent;
        }}
        #graph {{
            width: 100vw;
            height: 100vh;
            text-align: center;
        }}
        #controls {{
            position: absolute;
            top: 10px;
            left: 10px;
            background: rgba(255, 255, 255, 0.92);
            padding: 10px 14px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.15);
            border: 1px solid #e0e0e0;
            z-index: 10;
            font-size: 12px;
            line-height: 1.8;
        }}
    </style>
</head>
<body>
    <div id="controls">
        🖱️ Scroll to zoom<br>
        ✋ Drag to pan<br>
        🔗 Click name to open Wikipedia
    </div>
    <div id="graph"></div>
    <script>
        const b64 = "{b64_dot}";
        const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        const dotSrc = new TextDecoder("utf-8").decode(bytes);

        var graphviz = d3.select("#graph").graphviz()
            .width(window.innerWidth)
            .height(window.innerHeight)
            .fit(true)
            .renderDot(dotSrc);

        window.addEventListener("resize", () => {{
            graphviz
                .width(window.innerWidth)
                .height(window.innerHeight)
                .fit(true)
                .renderDot(dotSrc);
        }});
    </script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------

async def _generate_tree(mgp_id: int, dir_type: str):
    """Fetch graph data and produce DOT string."""
    graph = await math_genealogy.build_graph(mgp_id, dir_type, max_depth=99)
    nodes = graph.get("nodes", {})
    if not nodes:
        return None, "No data found for this MGP ID."
    dot_text = await math_genealogy.graph_to_dot(graph)
    return dot_text, len(nodes)


# ---------------------------------------------------------------------------
# UI – Sidebar
# ---------------------------------------------------------------------------

st.title("🌳 Mathematics Genealogy Tree")
st.markdown(
    "Visualize the academic family tree of any mathematician "
    "using the [Mathematics Genealogy Project](https://genealogy.math.ndsu.nodak.edu/)."
)

with st.sidebar:
    st.header("⚙️ Parameters")

    search_type = st.radio("Search by:", ["Name", "MGP ID"])

    if search_type == "Name":
        name_input = st.text_input("Mathematician name", value="Carl Friedrich Gauss")
        mgp_id_input = None
    else:
        name_input = None
        mgp_id_input = int(
            st.number_input("MGP numeric ID", value=17864, min_value=1, step=1)
        )

    direction = st.selectbox(
        "Traversal direction",
        ["ancestors", "descendants", "both"],
        help="Ancestors → advisors chain upward.  Descendants → students chain downward.",
    )

    generate_btn = st.button("Generate Tree 🚀", type="primary", use_container_width=True)

    st.markdown("---")
    st.caption(
        "Data sourced from the [Mathematics Genealogy Project](https://genealogy.math.ndsu.nodak.edu/). "
        "Wikipedia links are fetched automatically."
    )

# ---------------------------------------------------------------------------
# UI – Main area
# ---------------------------------------------------------------------------

if generate_btn:
    mgp_id: int | None = mgp_id_input

    # ------------------------------------------------------------------
    # Step 1 – resolve name → MGP ID (if searching by name)
    # ------------------------------------------------------------------
    if search_type == "Name":
        if not name_input or not name_input.strip():
            st.error("Please enter a mathematician's name.")
            st.stop()

        with st.spinner(f"Searching MGP for **{name_input}** …"):
            try:
                results = math_genealogy.search_mgp(name_input.strip())
            except Exception as exc:
                st.error(f"Search failed: {exc}")
                st.stop()

        if not results:
            st.error(
                f"No results found for **'{name_input}'**. "
                "Try a different spelling, or switch to MGP ID mode."
            )
            st.stop()

        if len(results) == 1:
            r = results[0]
            st.success(f"Found: **{r['name']}** — {r['university']} ({r['year']})")
            mgp_id = r["id"]
        else:
            st.warning(
                f"Found **{len(results)} matches** for '{name_input}'. "
                "Select the correct one below:"
            )
            options = {
                f"{r['name']} — {r['university']} ({r['year']})": r["id"]
                for r in results
            }
            choice = st.selectbox("Choose:", list(options.keys()))
            mgp_id = options[choice]
            if not st.button("Confirm selection"):
                st.stop()

    # ------------------------------------------------------------------
    # Step 2 – build graph & render
    # ------------------------------------------------------------------
    if mgp_id:
        with st.spinner(f"Building **{direction}** tree for MGP ID {mgp_id} …"):
            try:
                dot_text, node_count = run_async(_generate_tree(mgp_id, direction))
            except Exception as exc:
                st.error(f"Failed to build tree: {exc}")
                st.exception(exc)
                st.stop()

        if dot_text is None:
            st.error(str(node_count))
            st.stop()

        st.success(f"✅ Tree with **{node_count} nodes** generated successfully!")

        # Interactive D3 viewer
        components.html(get_viewer_html(dot_text), height=720, scrolling=False)

        # DOT code expander
        with st.expander("📄 Show Graphviz DOT source"):
            st.code(dot_text, language="dot")

        # Download button
        st.download_button(
            label="⬇️ Download .dot file",
            data=dot_text,
            file_name=f"genealogy_{mgp_id}.dot",
            mime="text/vnd.graphviz",
        )
