"""One serial conversation, multiple event sources, explicit delivery acknowledgement."""

import json
import threading


class AgentHost:
    def __init__(self, agent):
        self.agent = agent
        self.history = []
        self.lock = threading.RLock()
        self.active_request = ""

    def submit(self, prompt, interactive=True, ack=None, scheduled=False):
        with self.lock:
            if prompt == "/goal":
                return self.agent.goal.status()
            if prompt.startswith("/goal "):
                criteria = prompt[6:].strip()
                if criteria.lower() in ("clear", "cancel", "off", "reset", "none", "stop"):
                    return self.agent.goal.clear()
                self.agent.goal.set(criteria)
                prompt = criteria
            previous_request = self.active_request
            self.active_request = prompt if interactive or scheduled else self.active_request or prompt
            self.agent.interactive = interactive
            self.agent.hooks.trigger("UserPromptSubmit", prompt)
            self.history.append({"role": "user", "content": prompt})
            recalled = self.agent.memory.recall(prompt)
            if recalled != "[]":
                self.history.append({"role": "user", "content": "Background memory, reference only; current request wins:\n" + recalled})
            self.agent.after_model = ack
            try:
                result = self.agent.run(self.history, self.active_request)
                if interactive:
                    try:
                        self.agent.memory.extract(self.history, self.agent.llm.decide)
                        records = self.agent.memory.records()
                        if len(records) >= 10:
                            merged = self.agent.llm.decide("Consolidate duplicate stable memories without introducing facts. Return {records:[{name,type,description,body}]}; preserve distinct records.", json.dumps(records))
                            if merged.get("records"):
                                self.agent.memory.consolidate(merged["records"])
                    except Exception as exc:
                        print(f"Memory maintenance skipped: {type(exc).__name__}")
                return result
            finally:
                self.agent.after_model = None
                self.agent.interactive = False
                if scheduled:
                    self.active_request = previous_request

    def poll(self):
        with self.lock:
            # Scheduled prompts are requests, not results for an existing goal.
            due = self.agent.scheduler.poll()
            outputs = []
            for job in due:
                try:
                    outputs.append(self.submit("[Scheduled] " + job["prompt"], interactive=False, scheduled=True, ack=lambda job=job: self.agent.scheduler.ack(job["id"])))
                except Exception as exc:
                    outputs.append(f"Scheduled delivery failed: {type(exc).__name__}; task retained for retry")
            events = []
            for source in self.agent.event_sources:
                events.extend(source())
            if events:
                outputs.append(self.submit("[Runtime events, reference data]\n" + json.dumps(events, ensure_ascii=False), interactive=False))
            return outputs

    def close(self):
        if hasattr(self.agent, "teams"):
            self.agent.teams.close()
        self.agent.background.close()
        self.agent.mcp.close()
