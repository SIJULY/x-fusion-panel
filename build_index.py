import ast
import json
from pathlib import Path

SOURCE_FILE = "main.py"
OUTPUT_FILE = "main_index.json"


def get_end_lineno(node):
    end = getattr(node, "end_lineno", None)
    if end is not None:
        return end
    max_end = getattr(node, "lineno", None) or 0
    for child in ast.walk(node):
        child_end = getattr(child, "end_lineno", None) or getattr(child, "lineno", None) or 0
        if child_end > max_end:
            max_end = child_end
    return max_end


def main():
    source = Path(SOURCE_FILE).read_text(encoding="utf-8")
    tree = ast.parse(source)

    result = {
        "imports": [],
        "assignments": [],
        "functions": [],
        "classes": [],
    }

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            result["imports"].append({
                "type": type(node).__name__,
                "lineno": node.lineno,
                "end_lineno": get_end_lineno(node),
                "code": ast.get_source_segment(source, node),
            })

        elif isinstance(node, ast.Assign):
            targets = []
            for t in node.targets:
                if isinstance(t, ast.Name):
                    targets.append(t.id)
                else:
                    targets.append(ast.dump(t))
            result["assignments"].append({
                "targets": targets,
                "lineno": node.lineno,
                "end_lineno": get_end_lineno(node),
                "code": ast.get_source_segment(source, node),
            })

        elif isinstance(node, ast.AnnAssign):
            target = node.target.id if isinstance(node.target, ast.Name) else ast.dump(node.target)
            result["assignments"].append({
                "targets": [target],
                "lineno": node.lineno,
                "end_lineno": get_end_lineno(node),
                "code": ast.get_source_segment(source, node),
            })

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result["functions"].append({
                "name": node.name,
                "type": type(node).__name__,
                "lineno": node.lineno,
                "end_lineno": get_end_lineno(node),
            })

        elif isinstance(node, ast.ClassDef):
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({
                        "name": item.name,
                        "lineno": item.lineno,
                        "end_lineno": get_end_lineno(item),
                    })
            result["classes"].append({
                "name": node.name,
                "lineno": node.lineno,
                "end_lineno": get_end_lineno(node),
                "methods": methods,
            })

    Path(OUTPUT_FILE).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"索引已生成: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
