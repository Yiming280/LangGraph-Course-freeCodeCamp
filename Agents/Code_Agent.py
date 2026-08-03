import subprocess
import tempfile
import os
from typing import Annotated, Sequence, TypedDict
from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, ToolMessage, SystemMessage, HumanMessage, AIMessage
from langchain_ollama import ChatOllama
from langchain_core.tools import tool
from langgraph.graph.message import add_messages
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
import psycopg2
import psycopg2.extras

load_dotenv()


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


@tool
def python_executor(code: str) -> str:
    """Execute Python code and return the output.

    Use this tool to write and run Python code. The code will be executed
    in a temporary file and the stdout/stderr will be returned.

    Args:
        code: The Python code to execute.
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
            output = stdout if stdout else "(executed successfully, no output)"
        else:
            output = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}" if stdout else stderr
        return output
    except subprocess.TimeoutExpired:
        return "Error: Code execution timed out (30 seconds limit)."
    finally:
        os.unlink(tmp_path)


@tool
def query_database(sql: str) -> str:
    """Query the PostgreSQL database and return results.

    Use this tool to run SQL queries against the agile_dispatch database.
    The database connection is configured via .env (DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD).

    Args:
        sql: The SQL query to execute. Only SELECT queries are allowed.
    """
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql)
        if sql.strip().upper().startswith("SELECT"):
            rows = cur.fetchall()
            if not rows:
                return "(Query returned 0 rows)"
            return f"({len(rows)} rows)\n" + str(rows[:50])  # limit to 50 rows
        else:
            conn.commit()
            return f"Query executed. Rows affected: {cur.rowcount}"
    except Exception as e:
        conn.rollback()
        return f"Database error: {e}"
    finally:
        conn.close()


tools = [query_database, python_executor]

model = ChatOllama(model="qwen2.5").bind_tools(tools)


def model_call(state: AgentState) -> AgentState:
    system_prompt = SystemMessage(content="""You are a Code Agent - an AI assistant that writes and executes Python code to solve problems.

You have TWO tools available:

1. `query_database(sql)` — Run SQL queries against a PostgreSQL database (agile_dispatch).
   Use this to explore the database schema and fetch data.
   Common queries to start: "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
   Then: "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='your_table'"

2. `python_executor(code)` — Execute Python code to analyze data, create charts, or anything else.

Your workflow:
1. When given a task involving data, FIRST explore the database schema with query_database.
2. Once you understand the tables, query the data you need.
3. Use python_executor to process/analyze/visualize the results.
4. Explain findings clearly to the user.

Guidelines:
- Write clean, well-commented Python code.
- Handle edge cases and errors gracefully.
- If you need to install a package, include `subprocess.run(["pip", "install", "..."])` in your code.
- Always explain your approach before writing code or querying.
- If the code fails, read the error carefully and fix it.
""")
    response = model.invoke([system_prompt] + state["messages"])
    return {"messages": [response]}


def should_continue(state: AgentState):
    messages = state["messages"]
    last_message = messages[-1]
    if not last_message.tool_calls:
        return "end"
    else:
        return "continue"


graph = StateGraph(AgentState)
graph.add_node("agent", model_call)
graph.add_node("tools", ToolNode(tools=tools))

graph.set_entry_point("agent")

graph.add_conditional_edges(
    "agent",
    should_continue,
    {
        "continue": "tools",
        "end": END,
    },
)

graph.add_edge("tools", "agent")

app = graph.compile()


if __name__ == "__main__":
    print("\n===== CODE AGENT =====")
    print("I can write and execute Python code to solve your problems.")
    print("Type 'exit' to quit.\n")

    messages = []
    while True:
        user_input = input("You: ")
        if user_input.lower() == "exit":
            break

        messages.append(HumanMessage(content=user_input))

        print("\n--- Agent is thinking... ---\n")
        result = app.invoke({"messages": messages})

        # Extract only new messages added by the agent
        new_messages = result["messages"][len(messages):]
        messages = result["messages"]

        for msg in new_messages:
            if isinstance(msg, AIMessage):
                if msg.content:
                    print(f"🤖 Agent: {msg.content}")
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    for tc in msg.tool_calls:
                        print(f"  🔧 Calling tool: {tc['name']}")
                        code_preview = tc['args'].get('code', '')
                        if len(code_preview) > 200:
                            code_preview = code_preview[:200] + "..."
                        print(f"  📝 Code:\n{code_preview}")
            elif isinstance(msg, ToolMessage):
                output = str(msg.content)
                if len(output) > 500:
                    output = output[:500] + "..."
                print(f"  📤 Output:\n{output}")

        print()
