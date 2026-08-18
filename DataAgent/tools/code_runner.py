"""代码执行工具：在子进程中运行 Python 代码并返回输出。"""
import os
import subprocess
import tempfile

from langchain_core.tools import tool


@tool
def python_executor(code: str) -> str:
    """执行 Python 代码并返回 stdout/stderr。

    用它做通用分析、计算、批处理等。代码在临时文件中以子进程运行，30 秒超时。
    注意：该工具无网络访问限制，请在受信环境运行。

    Args:
        code: 要执行的 Python 代码。
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["python", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            return stdout if stdout else "(executed successfully, no output)"
        return f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}" if stdout else stderr
    except subprocess.TimeoutExpired:
        return "Error: Code execution timed out (30 seconds limit)."
    finally:
        os.unlink(tmp_path)
