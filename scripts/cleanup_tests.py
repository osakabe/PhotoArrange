import ast
import os
import shutil

os.makedirs("scripts/tests_manual", exist_ok=True)
for file in os.listdir("tests"):
    if not file.endswith(".py") or file == "conftest.py":
        continue
    path = os.path.join("tests", file)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        tree = ast.parse(content)
        has_test_func = any(
            isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
            for node in ast.walk(tree)
        )
        has_main = (
            "if __name__ == '__main__':" in content or 'if __name__ == "__main__":' in content
        )
        is_pytest = ("import pytest" in content or has_test_func) and not has_main
        if not is_pytest:
            print(f"Moving {file} to scripts/tests_manual/")
            shutil.move(path, os.path.join("scripts/tests_manual", file))
    except SyntaxError:
        print(f"SyntaxError in {file}")
        shutil.move(path, os.path.join("scripts/tests_manual", file))
