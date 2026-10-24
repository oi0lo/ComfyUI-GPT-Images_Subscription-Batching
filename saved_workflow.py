"""Read a widget's value exactly as the frontend saved it in the workflow sent with a run.

ComfyUI converts every on/off input with bool() before the node runs, so a switch that
holds leftover text (a workflow saved by an older node version, restored by position)
reaches the node as True. The workflow the frontend sends along with each run still has
the raw value. Standard library only; no ComfyUI imports, so it can be tested on its own.
"""

from __future__ import annotations

MISSING = object()  # the workflow, the node or the value is not available


def saved_value(extra_pnginfo, unique_id, name: str):
    """The raw saved value of widget `name` on node `unique_id`, or MISSING.

    MISSING when the run carries no workflow (API use), the frontend does not write
    widgets_values_named, or the node is not at the top level (inside a subgraph).
    """
    workflow = extra_pnginfo.get("workflow") if isinstance(extra_pnginfo, dict) else None
    nodes = workflow.get("nodes") if isinstance(workflow, dict) else None
    if not isinstance(nodes, list) or unique_id is None:
        return MISSING
    for node in nodes:
        if isinstance(node, dict) and str(node.get("id")) == str(unique_id):
            named = node.get("widgets_values_named")
            if isinstance(named, dict) and name in named:
                return named[name]
            return MISSING
    return MISSING


def leftover_text(value) -> str | None:
    """A short quote of the value when it is not a real on/off value, else None."""
    if value is MISSING or isinstance(value, bool):
        return None
    text = " ".join(str(value).split())
    return text if len(text) <= 60 else text[:57] + "..."
