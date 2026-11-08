"""AST validation, function splitting, and sandboxed namespace for scene code."""
import re
import ast
import math
import textwrap
from typing import Dict, Any, Optional, Tuple, List

from .mathutil import clamp

_SAFE_BUILTINS: Dict[str, Any] = {
    "range": range, "len": len, "min": min, "max": max, "abs": abs,
    "int": int, "float": float, "str": str, "round": round,
    "enumerate": enumerate, "zip": zip, "list": list, "dict": dict, "tuple": tuple,
    "sum": sum, "pow": pow, "bool": bool, "any": any, "all": all,
    "sorted": sorted, "divmod": divmod, "reversed": reversed, "set": set,
    "map": map, "filter": filter, "isinstance": isinstance,
    "print": (lambda *a, **k: None),
    "True": True, "False": False, "None": None,
}

_FORBIDDEN_NAMES = {
    "exec", "eval", "open", "compile", "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "input", "breakpoint", "exit", "quit", "memoryview", "type", "object", "super",
}


def validate_scene_source(src: str) -> Optional[str]:
    """
    Static gate for AI-written drawing code. Returns an error string, or None when acceptable.
    This is a guard rail against accidents and obvious escapes (imports, dunder access, file/eval calls,
    unbounded `while` loops). It is not a hardened sandbox: only run models you trust.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError as err:
        return f"SyntaxError: {err}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "import statements are not allowed (math is already available)"
        if isinstance(node, ast.While):
            return "'while' loops are not allowed; use 'for ... in range(...)'"
        if isinstance(node, ast.Global):
            return "'global' is not allowed"
        if isinstance(node, ast.Name) and (node.id.startswith("__") or node.id in _FORBIDDEN_NAMES):
            return f"name '{node.id}' is not allowed"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"attribute '{node.attr}' is not allowed"
    return None


def new_scene_namespace() -> Dict[str, Any]:
    return {"__builtins__": dict(_SAFE_BUILTINS), "math": math, "clamp": clamp}


def _trim_to_function_body(chunk: str) -> str:
    """
    Cuts a regex-sliced chunk at the end of its own function. A function body is the `def` line plus every
    following line that is blank or indented; the first unindented code line (a stray `import math`, a
    `print(...)`, prose from the model) belongs to something else. Without this, a stray top-level line
    glued to the PREVIOUS function made that perfectly valid function fail validation.
    A line that merely closes a bracket opened inside the signature/body (`)`, `]`, `}`) is kept.
    """
    lines = chunk.splitlines()
    keep = 1                                  # the `def` line itself
    for idx in range(1, len(lines)):
        ln = lines[idx]
        if not ln.strip():
            continue
        if ln[0] in " \t" or ln.lstrip()[0] in ")]}":
            keep = idx + 1
            continue
        break
    return "\n".join(lines[:keep]).rstrip()


def _salvage_functions(py_code: str, log_fn) -> Tuple[Dict[str, str], Dict[str, str]]:
    """If the whole module fails to parse, keep every top-level function that parses on its own."""
    helpers: Dict[str, str] = {}
    beats: Dict[str, str] = {}
    matches = list(re.finditer(r"^[ \t]*def\s+(\w+)\s*\(", py_code, flags=re.MULTILINE))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(py_code)
        chunk = textwrap.dedent(_trim_to_function_body(py_code[m.start():end]))
        try:
            ast.parse(chunk)
        except SyntaxError as err:
            log_fn(f"[CODE WARN] '{m.group(1)}' skipped, unrecoverable syntax error: {err}")
            continue
        (beats if re.fullmatch(r"beat_\d+", m.group(1)) else helpers)[m.group(1)] = chunk
    return helpers, beats


def split_scene_code(py_code: str, log_fn) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Splits generated code into (helpers, beats), each {name: source}."""
    try:
        tree = ast.parse(py_code)
    except SyntaxError as err:
        log_fn(f"[CODE WARN] Module has a syntax error ({err}); salvaging function by function...")
        return _salvage_functions(py_code, log_fn)

    helpers: Dict[str, str] = {}
    beats: Dict[str, str] = {}
    for i, node in enumerate(tree.body):
        seg = ast.get_source_segment(py_code, node)
        if not seg:
            continue
        if isinstance(node, ast.FunctionDef):
            (beats if re.fullmatch(r"beat_\d+", node.name) else helpers)[node.name] = seg
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            helpers[f"__const_{i}"] = seg
    return helpers, beats


def register_source(namespace: Dict[str, Any], name: str, src: str, log_fn) -> bool:
    err = validate_scene_source(src)
    if err:
        log_fn(f"[CODE REJECTED] {name}: {err}")
        return False
    try:
        exec(src, namespace)
    except Exception as exc:
        log_fn(f"[CODE ERROR] {name} failed to load: {type(exc).__name__}: {exc}")
        return False
    if name.startswith("__const_"):
        return True
    return callable(namespace.get(name))


def new_scene_sources() -> Dict[str, Dict[str, str]]:
    return {"helpers": {}, "beats": {}}


def build_namespace(sources: Dict[str, Dict[str, str]], log_fn) -> Dict[str, Any]:
    ns = new_scene_namespace()
    for name, src in sources.get("helpers", {}).items():
        register_source(ns, name, src, log_fn)
    for name, src in sources.get("beats", {}).items():
        register_source(ns, name, src, log_fn)
    return ns


def add_generated_code(plan: dict, namespace: Dict[str, Any], py_code: str, log_fn) -> List[str]:
    """Parses a code block from the model, loads what is valid, and records the sources on the plan."""
    helpers, beats = split_scene_code(py_code, log_fn)
    store = plan.setdefault("scene_sources", new_scene_sources())
    for name, src in helpers.items():
        if name in store["helpers"] and store["helpers"][name] != src and not name.startswith("__const_"):
            log_fn(f"[CODE WARN] Helper '{name}' redefined by a later batch; the newer version wins.")
        if register_source(namespace, name, src, log_fn):
            store["helpers"][name] = src
    loaded: List[str] = []
    for name, src in beats.items():
        if register_source(namespace, name, src, log_fn):
            store["beats"][name] = src
            loaded.append(name)
    return loaded


def register_beat_source(plan: dict, namespace: Dict[str, Any], name: str, src: str, log_fn) -> bool:
    if register_source(namespace, name, src, log_fn):
        plan.setdefault("scene_sources", new_scene_sources())["beats"][name] = src
        return True
    return False


from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional, Union

def write_scene_module_file(plan: dict, path: Union[str, Path] = "generated_scenes.py"):
    store = plan.get("scene_sources") or new_scene_sources()
    parts = ["# Auto-generated by Nalarka Motion Graphics Studio v8.1\nimport math\n"]
    parts += list(store.get("helpers", {}).values())
    for name in sorted(store.get("beats", {})):
        parts.append(store["beats"][name])
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n")




