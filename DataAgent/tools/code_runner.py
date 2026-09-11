"""代码执行工具：在子进程中运行 Python 代码并返回输出。"""
import os
import re
import subprocess
import tempfile

from langchain_core.tools import tool

# 匹配 data URI（如 data:image/png;base64,....）——防止模型把图片 base64 打进
# stdout，再被回填进上下文/回答，导致无限输出乱码。换成占位符即可。
_DATA_URI_RE = re.compile(r"data:[A-Za-z0-9./+;_-]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)

# stdout/stderr 总长上限：超出截断，避免超长结果撑爆上下文
_MAX_OUTPUT = 20000

# 子进程工作目录固定为项目根（DataAgent/），这样代码里的相对路径
# static/charts/xxx.png 无论服务从哪启动，都能正确落到静态目录、被前端展示。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _strip_data_uris(text: str) -> str:
    return _DATA_URI_RE.sub("[图片二进制已省略，请用图表工具而非打印 base64]", text)


def _clamp(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + "\n…(输出过长，已截断)"


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
            cwd=_PROJECT_ROOT,
        )
        stdout = _strip_data_uris(result.stdout.strip())
        stderr = _strip_data_uris(result.stderr.strip())

        if result.returncode == 0:
            return _clamp(stdout) if stdout else "(executed successfully, no output)"
        return _clamp(f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}" if stdout else stderr)
    except subprocess.TimeoutExpired:
        return "Error: Code execution timed out (30 seconds limit)."
    finally:
        os.unlink(tmp_path)
