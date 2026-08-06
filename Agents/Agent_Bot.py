from typing import TypedDict, List
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_ollama import ChatOllama
from langgraph.graph import StateGraph, START, END
from dotenv import load_dotenv

load_dotenv()

class AgentState(TypedDict):
    messages: List[HumanMessage]

llm = ChatOllama(
    base_url="http://10.8.20.83:11435",  # 远程 Ollama 服务器地址和映射端口
    model="qwen3.6:27b",                # 模型名称
    temperature=0.2,
)

system_prompt = SystemMessage(content=f"""
   你是一个具备深厚逻辑推理能力的大语言模型。
    无论用户提出什么问题，你都【必须】严格按照以下格式回答：

    <think>
    在这里写下你的详细思考过程、逻辑拆解、边界条件分析与方案对比。
    </think>

    在这里给出最终对用户展示的正式回答。""")

def process(state: AgentState) -> AgentState:
    # response = llm.invoke(state["messages"])
    # print("思维链:", response.additional_kwargs.get("reasoning_content"))
    for chunk in llm.stream(state["messages"]):
        print(chunk.content, end="", flush=True)
    # print(f"\nAI: {response.content}")
    return state

graph = StateGraph(AgentState)
graph.add_node("process", process)
graph.add_edge(START, "process")
graph.add_edge("process", END) 
agent = graph.compile()

user_input = input("Enter: ")
while user_input != "exit":
    user_message = HumanMessage(content=user_input)
    all_messages = [system_prompt] + [user_message]
    agent.invoke({"messages": all_messages})
    user_input = input("\nEnter: ")
