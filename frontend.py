import os
import json
from pathlib import Path
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

DATA_DIR = Path("data")


def scan_data_files():
    """扫描 data/ 目录下的所有 jsonl 文件"""
    queries = []
    
    if not DATA_DIR.exists():
        return queries
    
    # 遍历所有日期目录
    for date_dir in sorted(DATA_DIR.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        
        date_str = date_dir.name
        
        # 遍历该日期下的所有 jsonl 文件
        for jsonl_file in sorted(date_dir.glob("*.jsonl")):
            try:
                # 读取第一行获取 query 信息
                with open(jsonl_file, 'r', encoding='utf-8') as f:
                    first_line = f.readline()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("type") == "query":
                            queries.append({
                                "query": data.get("query", ""),
                                "md5": data.get("md5", ""),
                                "date": data.get("date", date_str),
                                "file_path": str(jsonl_file.relative_to(DATA_DIR)),
                                "file_name": jsonl_file.name
                            })
            except Exception as e:
                print(f"Error reading {jsonl_file}: {e}")
                continue
    
    return queries


def load_query_data(file_path: str):
    """加载指定文件的所有数据"""
    full_path = DATA_DIR / file_path
    
    if not full_path.exists():
        return None
    
    query_info = None
    messages = []
    final_result = None
    all_search_results = {}  # idx -> search_result 的映射
    
    with open(full_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if line_num == 0 and data.get("type") == "query":
                    query_info = data
                elif data.get("type") == "message":
                    message = data.get("message")
                    messages.append(message)
                    # 收集所有 search_results
                    if message.get("role") == "tool" and message.get("search_results"):
                        for result in message.get("search_results", []):
                            idx = result.get("idx")
                            if idx is not None:
                                all_search_results[idx] = result
                elif data.get("type") == "final":
                    final_result = data.get("content", "")
            except json.JSONDecodeError as e:
                print(f"Error parsing line {line_num + 1}: {e}")
                continue
    
    return {
        "query_info": query_info,
        "messages": messages,
        "final_result": final_result,
        "search_results": all_search_results  # 所有搜索结果
    }


@app.route('/')
def index():
    """主页面"""
    return render_template('index.html')


@app.route('/api/queries')
def get_queries():
    """获取所有查询列表"""
    queries = scan_data_files()
    return jsonify({"queries": queries})


@app.route('/api/query/<path:file_path>')
def get_query_data(file_path):
    """获取指定查询的详细数据"""
    data = load_query_data(file_path)
    if data is None:
        return jsonify({"error": "File not found"}), 404
    return jsonify(data)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5005)

