from abc import ABC, abstractmethod

class Tool(ABC):
    name = ""
    description = ""

    @abstractmethod
    def run(self, **kwargs):
        pass

    def schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        }