import json

class Agent:
    def __init__(self,llm,registry):
        self.llm=llm
        self.registry=registry

    def run(self,messages):
        while True:
            response=self.llm.chat(messages,self.registry.schemas())
            messages.append(response.model_dump(exclude_none=True))
            tool_calls=response.tool_calls or []
            if not tool_calls:
                return response.content

            results=[]
            for block in tool_calls:
                args=json.loads(block.function.arguments)
                print(f"\033[33m$ {args}\033[0m")
                tool=self.registry.get(block.function.name)
                result=tool.run(**args)
                print(result[:200])
                results.append({
                    "role": "tool",
                    "tool_call_id": block.id,
                    "content": result,
                })
            messages.extend(results)