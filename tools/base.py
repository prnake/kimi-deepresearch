from abc import ABC
class Tool(ABC):
    """
    Tool 基础类。每个工具需继承该类并实现 to_openai_tool() 方法。
    """
    name: str = ""
    description: str = ""
    parameters: dict = {}
    required: list = []

    @classmethod
    def to_openai_tool(cls):
        """
        以 OpenAI function 对接格式返回工具定义
        返回示例：
        {
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": {
                    "type": "object",
                    "properties": cls.parameters,
                    "required": cls.required
                }
            },
            "type": "function"
        }
        """
        function_def = {
            "name": cls.name,
            "description": cls.description,
            "parameters": {
                "type": "object",
                "properties": cls.parameters,
                "required": cls.required or []
            }
        }
        return {
            "function": function_def,
            "type": "function"
        }
