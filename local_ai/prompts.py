"""Use each model's local tokenizer/template, with a token-based budget."""

from dataclasses import dataclass


class PromptError(ValueError):
    pass


@dataclass
class Prompt:
    tokens: list
    prefix: str = ""
    removed_messages: int = 0


def validate_messages(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise PromptError("messages must contain 1..64 messages")
    result = []
    total = 0
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) - {"role", "content"}:
            raise PromptError("Only text role/content messages are supported")
        role, content = item.get("role"), item.get("content")
        if not isinstance(role, str) or role not in {"system", "user", "assistant"} or not isinstance(content, str):
            raise PromptError("Invalid message role or text")
        if role == "system" and index != 0:
            raise PromptError("System prompt is allowed only at the beginning")
        if not content.strip() or "\0" in content:
            raise PromptError("Message text is empty or contains NUL")
        total += len(content)
        if total > 32768:
            raise PromptError("Conversation text is too large")
        result.append({"role": role, "content": content.strip()})
    if result[-1]["role"] != "user":
        raise PromptError("The final message must be from the user")
    conversation = result[1:] if result[0]["role"] == "system" else result
    if any(item["role"] != ("user" if i % 2 == 0 else "assistant")
           for i, item in enumerate(conversation)):
        raise PromptError("User and assistant messages must alternate")
    return result


class LocalPrompts:
    def __init__(self, loader=None):
        self.loader = loader
        self.cache = {}

    def tokenizer(self, model):
        key = str(model.tokenizer)
        if key not in self.cache:
            if self.loader is None:
                from transformers import AutoTokenizer
                loader = AutoTokenizer.from_pretrained
            else:
                loader = self.loader
            self.cache[key] = loader(key, local_files_only=True, trust_remote_code=False)
        return self.cache[key]

    def prepare(self, model, messages):
        messages = validate_messages(messages)
        if messages[0]["role"] != "system" and model.system:
            messages.insert(0, {"role": "system", "content": model.system})
        tokenizer = self.tokenizer(model)
        removed = 0
        while True:
            text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            ids = tokenizer(text, add_special_tokens=False)["input_ids"]
            if (not isinstance(ids, list) or not ids or any(type(i) is not int or not 0 <= i <= 2147483647 for i in ids)):
                raise PromptError("Tokenizer did not return valid int32 token IDs")
            if len(ids) <= model.context - model.max_new_tokens:
                break
            start = 1 if messages[0]["role"] == "system" else 0
            if len(messages) - start <= 1:
                raise PromptError("The latest question exceeds this model's token budget; shorten it")
            # Drop a complete old user/assistant pair, keeping the prompt valid.
            del messages[start:start + 2]
            removed += 2
        # R1 templates may already open the hidden reasoning block in the
        # prompt. Restore that marker on the response so clients can hide it.
        prefix = "<think>" if (model.name.startswith("deepseek-r1")
                                and text.rstrip().endswith("<think>")) else ""
        return Prompt(ids, prefix, removed)
