from .base import Tool
import os
import re
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Search(Tool):
    name = "search"
    description = "Web Search API, works like Google Search."
    parameters = {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Search directly by queries. All queries will be searched in parallel. If you want to search with multiple keywords, put them in a single query."
        },
    }
    required = ["queries"]

    search_url: str = os.getenv("SEARCH_URL", "https://s.jina.ai")
    search_api_key: str = os.getenv("SEARCH_API_KEY")

    toolcall_id: int = 0
    search_idx: int = 0
    search_results: list[dict] = []
    search_results_set: set[str] = set()

    def __init__(self, max_length: int = 30000):

        self.max_length = max_length

        self.headers = {
            "X-Engine": "direct",
            "X-Timeout": "20",
            "X-Retain-Images": "none",
            "Accept": "application/json"
        }
        if self.search_api_key:
            self.headers["Authorization"] = f"Bearer {self.search_api_key}"
    
    def get_domain(self, url: str):
        if "://" in url:
            return url.split("/")[2]
        return ""
    
    def remove_text_links(self, text: str):
        return re.sub(r'\[(.*?)\]\((.*?)\)', r'\1', text)

    def query(self, q):
        try:
            response = requests.get(self.search_url, params={"q": q}, headers=self.headers, timeout=20)
            if response.status_code != 200:
                logger.error(f"Search query failed: {response.text}")
                return []
            data = response.json()
            return data.get("data", [])
        except Exception:
            import traceback
            traceback.print_exc()
            return []

    def run(self, queries: list[str], **kwargs):
        results = []
        seen_urls = set()
        full_prompt_blocks = []

        with ThreadPoolExecutor() as executor:
            future_to_query = {executor.submit(self.query, q): q for q in queries}
            for future in as_completed(future_to_query):
                query = future_to_query[future]
                query_results = future.result()
                
                current_results = []
                current_prompt_blocks = [f"[搜索关键词]:{query}",]

                for result in query_results:
                    title = result.get("title", "")
                    url = result.get("url", "")
                    content = (result.get("content", "") + "\n" + result.get("description", "")).strip()
                    content = self.remove_text_links(content)
                    pub_time = result.get("publishedTime", "")
                    site_name = result.get("siteName", self.get_domain(url))

                    unique_site_key = f"{title}|{url}"
                    if unique_site_key in self.search_results_set:
                        continue
                    self.search_results_set.add(unique_site_key)
                    
                    current_results.append({
                        "title": title,
                        "url": url,
                        "content": content,
                        "pub_time": pub_time,
                        "site_name": site_name,
                        "idx": self.search_idx
                    })

                    current_prompt_blocks.extend(
                        [
                            f"[编号]:[^{self.search_idx}^]",
                            f"[标题]:{title}",
                            f"[网站名称]:{site_name}",
                            f"[日期]:{pub_time}",
                            f"[网站链接]:{url}",
                            f"[片段]:\n{content}",
                            "",
                        ]
                    )                    
                    self.search_idx += 1

                    if len("\n".join(current_prompt_blocks)) > self.max_length // len(queries):
                        break
                
                self.search_results.extend(current_results)
                full_prompt_blocks.extend(current_prompt_blocks)


        full_prompt = "\n".join(full_prompt_blocks)
        return {
            "tool_call_id": f"search:{self.toolcall_id}",
            "content": full_prompt
        }

