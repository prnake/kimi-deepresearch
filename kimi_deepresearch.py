import os
import json
import hashlib
import openai
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from prompt import get_deep_research_prompt
from tools.search import Search

load_dotenv()


class ChatClient:
    def __init__(self, base_url: str, api_key: str):
        """初始化客户端"""
        self.base_url = base_url
        self.api_key = api_key
        self.openai = openai.Client(
            base_url=base_url,
            api_key=api_key,
        )
        self.model = "kimi-k2-thinking"
        self.search = Search()


def message_to_dict(message):
    """将 OpenAI Message 对象转换为字典"""
    if isinstance(message, dict):
        return message
    
    result = {
        "role": message.role,
    }
    
    if hasattr(message, "content") and message.content:
        result["content"] = message.content
    
    if hasattr(message, "tool_calls") and message.tool_calls:
        result["tool_calls"] = [
            {
                "id": tc.id,
                "type": tc.type,
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                }
            }
            for tc in message.tool_calls
        ]
    
    if hasattr(message, "reasoning_content") and message.reasoning_content:
        result["reasoning_content"] = message.reasoning_content
    
    return result


def compact_old_tool_messages(messages, keep_rounds=3):
    """
    只保留最近 keep_rounds 轮的 toolcall 结果，历史结果用 compact 格式替代
    """
    # 找到所有 tool 消息的位置
    tool_message_indices = []
    for i, msg in enumerate(messages):
        # 处理字典和对象两种格式
        role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
        if role == "tool":
            tool_message_indices.append(i)
    
    # 如果 tool 消息数量 <= keep_rounds，不需要压缩
    if len(tool_message_indices) <= keep_rounds:
        return messages
    
    # 需要压缩的 tool 消息数量
    num_to_compact = len(tool_message_indices) - keep_rounds
    
    # 压缩旧的 tool 消息
    for i in range(num_to_compact):
        idx = tool_message_indices[i]
        old_msg = messages[idx]
        # 获取 tool_call_id
        if isinstance(old_msg, dict):
            tool_call_id = old_msg.get("tool_call_id", "")
        else:
            tool_call_id = getattr(old_msg, "tool_call_id", "")
        
        messages[idx] = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": "历史tool_call已隐藏",
            "compact": True
        }
    
    return messages


def load_existing_data(file_path: Path):
    """加载已有的数据，用于断点续跑"""
    if not file_path.exists():
        return None, [], None
    
    messages = []
    query_info = None
    final_result = None
    
    with open(file_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if line_num == 0 and data.get("type") == "query":
                    # 第一行是 query 信息
                    query_info = data
                elif data.get("type") == "message":
                    # 后续行是消息
                    messages.append(data.get("message"))
                elif data.get("type") == "final":
                    # 最终结果
                    final_result = data.get("content", "")
            except json.JSONDecodeError as e:
                print(f"警告: 解析第 {line_num + 1} 行失败: {e}")
                continue
    
    return query_info, messages, final_result


def save_data(file_path: Path, data_type: str, data: dict):
    """增量保存数据到 jsonl 文件"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(file_path, 'a', encoding='utf-8') as f:
        record = {
            "type": data_type,
            "timestamp": datetime.now().isoformat(),
            **data
        }
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
        f.flush()


def deep_research(query: str, max_iterations: int = 300, data_dir: str = "data"):
    """
    深度研究函数
    
    Args:
        query: 用户查询
        max_iterations: 最大迭代次数
        data_dir: 数据保存目录
    
    Returns:
        最终的回答内容
    """
    # 计算 query 的 md5
    query_md5 = hashlib.md5(query.encode('utf-8')).hexdigest()
    
    # 获取当前日期
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # 构建文件路径
    file_path = Path(data_dir) / date_str / f"{query_md5}.jsonl"
    
    # 初始化客户端和工具（无论是否断点续跑都需要）
    base_url = os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1")
    api_key = os.getenv("MOONSHOT_API_KEY")
    
    if not api_key:
        raise ValueError("MOONSHOT_API_KEY 环境变量未设置，请先设置 API 密钥")
    
    print(f"Base URL: {base_url}")
    print(f"API Key: {api_key[:10]}...{api_key[-10:] if len(api_key) > 20 else api_key}\n")
    
    client = ChatClient(base_url, api_key)
    
    # 加载 Search 工具
    search_tool = Search.to_openai_tool()
    all_tools = [search_tool]
    print(f"已加载工具: {Search.name}\n")
    
    # 尝试加载已有数据（断点续跑）
    query_info, existing_messages, final_result = load_existing_data(file_path)
    
    # 如果已经完成，直接返回结果
    if final_result:
        print(f"检测到已完成的任务，直接返回结果...")
        print(f"原始查询: {query_info.get('query') if query_info else query}")
        return final_result
    
    if query_info:
        print(f"检测到已有数据，从断点继续运行...")
        print(f"原始查询: {query_info.get('query')}")
        print(f"已恢复 {len(existing_messages)} 条消息\n")
        messages = existing_messages
        
        # 检查最后一条 assistant 消息是否已完成（没有 tool_calls）
        last_assistant_msg = None
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                last_assistant_msg = msg
                break
        
        if last_assistant_msg and not last_assistant_msg.get("tool_calls"):
            # 已经完成，返回最后一条消息的内容
            print("检测到任务已完成，返回最后的结果...")
            return last_assistant_msg.get("content", "")
        
        # 计算已完成的迭代次数（assistant 消息且包含 tool_calls 的数量）
        start_iteration = len([m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")])
    else:
        # 初始化消息列表
        messages = [
            {
                "role": "system",
                "content": get_deep_research_prompt(),
            },
        ]
        
        # 添加用户查询
        messages.append({
            "role": "user",
            "content": query
        })
        
        # 保存 query 信息（第一行）
        save_data(file_path, "query", {
            "query": query,
            "md5": query_md5,
            "date": date_str
        })
        
        print(f"用户请求: {query}\n")
        start_iteration = 0
    
    # 主循环
    for iteration in range(start_iteration, max_iterations):
        # 压缩历史 tool 消息（只保留最近3轮）
        messages = compact_old_tool_messages(messages, keep_rounds=3)
        
        # 调用模型
        try:
            # 将消息转换为 API 格式
            api_messages = []
            for msg in messages:
                if isinstance(msg, dict):
                    api_messages.append(msg)
                else:
                    api_messages.append(message_to_dict(msg))
            
            completion = client.openai.chat.completions.create(
                model=client.model,
                messages=api_messages,
                max_tokens=1024 * 32,
                tools=all_tools,
                temperature=1.0,
            )
        except openai.AuthenticationError as e:
            print(f"认证错误: {e}")
            print("请检查 API key 是否正确，以及 API key 是否有权限访问该端点")
            raise
        except Exception as e:
            print(f"调用模型时发生错误: {e}")
            raise
        
        # 获取响应
        message = completion.choices[0].message
        
        # 打印思考过程
        if hasattr(message, "reasoning_content"):
            print(f"=============第 {iteration + 1} 轮思考开始=============")
            reasoning = getattr(message, "reasoning_content")
            if reasoning:
                print(reasoning[:500] + "..." if len(reasoning) > 500 else reasoning)
            print(f"=============第 {iteration + 1} 轮思考结束=============\n")
        
        # 转换为字典并保存
        message_dict = message_to_dict(message)
        messages.append(message_dict)
        save_data(file_path, "message", {"message": message_dict})
        
        # 如果模型没有调用工具，说明对话结束
        if not message.tool_calls:
            print("=============最终回答=============")
            print(message.content)
            # 保存最终结果
            save_data(file_path, "final", {
                "content": message.content,
                "iteration": iteration + 1
            })
            break
        
        # 处理工具调用
        print(f"模型决定调用 {len(message.tool_calls)} 个工具:\n")
        
        for tool_call in message.tool_calls:
            func_name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)
            
            print(f"调用工具: {func_name}")
            print(f"参数: {json.dumps(args, ensure_ascii=False, indent=2)}")
            
            # 只处理 search 工具
            if func_name != Search.name:
                print(f"错误: 未知工具 {func_name}")
                continue
            
            # 调用 Search 工具
            queries = args.get("queries", [])
            
            # 记录调用前的 search_idx，用于确定本次调用新增的结果
            start_idx = client.search.search_idx if hasattr(client.search, 'search_idx') else 0
            
            result = client.search.run(queries)
            
            # 打印结果（截断过长内容）
            content = result.get("content", "")
            if len(content) > 200:
                print(f"工具结果: {content[:200]}...\n")
            else:
                print(f"工具结果: {content}\n")
            
            # 获取本次调用新增的 search_results（用于前端渲染引用）
            current_search_results = []
            if hasattr(client.search, 'search_results') and client.search.search_results:
                # 只保存本次调用新增的结果（idx >= start_idx）
                current_search_results = [
                    r for r in client.search.search_results 
                    if r.get('idx', -1) >= start_idx
                ]
            
            # 添加工具结果到消息列表
            tool_message = {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": content,
                "search_results": current_search_results  # 保存搜索结果数据
            }
            messages.append(tool_message)
            # 保存工具调用结果
            save_data(file_path, "message", {"message": tool_message})
    
    print("\n对话完成！")
    return message.content if hasattr(message, 'content') and message.content else ""


if __name__ == "__main__":
    # 示例使用
    user_request = "请帮我生成一份今日新闻报告，包含重要的科技、经济和社会新闻。"
    result = deep_research(user_request)
    print(f"\n最终结果: {result}")
