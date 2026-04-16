import json
from pathlib import Path

SOURCE_FILE = "main.py"
INDEX_FILE = "main_index.json"
OUTPUT_DIR = Path("main_slices")


def main():
    source = Path(SOURCE_FILE).read_text(encoding="utf-8").splitlines()
    index = json.loads(Path(INDEX_FILE).read_text(encoding="utf-8"))

    OUTPUT_DIR.mkdir(exist_ok=True)

    for func in index["functions"]:
        start = func["lineno"] - 1
        end = func["end_lineno"]
        code = "\n".join(source[start:end])
        filename = OUTPUT_DIR / f"func_{func['name']}_{func['lineno']}_{func['end_lineno']}.py"
        filename.write_text(code, encoding="utf-8")

    for cls in index["classes"]:
        start = cls["lineno"] - 1
        end = cls["end_lineno"]
        code = "\n".join(source[start:end])
        filename = OUTPUT_DIR / f"class_{cls['name']}_{cls['lineno']}_{cls['end_lineno']}.py"
        filename.write_text(code, encoding="utf-8")

    print(f"切片完成，输出目录: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
