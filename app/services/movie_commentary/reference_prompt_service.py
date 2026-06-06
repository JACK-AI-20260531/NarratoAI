from __future__ import annotations

import json
import re
from typing import Any

from app.services.movie_commentary.models import ReferenceStyleProfile


class ReferencePromptService:
    SYSTEM_PROMPT = "你是一名电影解说创作分析师。你只提炼结构、节奏和创作方法，不复制参考文案原句。"

    async def analyze_references(
        self,
        reference_texts: list[str],
        *,
        creator_positioning: str = "电影解说创作者",
        provider: str | None = None,
    ) -> ReferenceStyleProfile:
        cleaned_texts = [text.strip() for text in reference_texts if text and text.strip()]
        if not cleaned_texts:
            raise ValueError("reference_texts 不能为空")

        prompt = self.build_analysis_prompt(cleaned_texts, creator_positioning=creator_positioning)
        from app.services.llm.unified_service import UnifiedLLMService

        response = await UnifiedLLMService.generate_text(
            prompt=prompt,
            system_prompt=self.SYSTEM_PROMPT,
            provider=provider,
            temperature=0.7,
            response_format="json",
        )
        return self.parse_profile(response)

    def build_analysis_prompt(self, reference_texts: list[str], *, creator_positioning: str) -> str:
        samples = []
        for index, text in enumerate(reference_texts, 1):
            samples.append(f"## 参考样片 {index}\n{self.truncate_text(text, 4500)}")
        sample_block = "\n\n".join(samples)
        return f"""
请分析以下电影解说参考文本，提炼可复用的原创创作方法，并生成一个适合“{creator_positioning}”使用的原创电影解说提示词模板。

重要边界：
1. 不要复制、改写或复刻参考文本中的具体句子。
2. 只总结开头方式、叙事结构、节奏、悬念规则、句式结构和原创约束。
3. 输出的提示词必须要求生成原创表达。
4. 输出必须是 JSON，不要附加解释。

JSON 格式：
{{
  "opening_type": "开头类型",
  "narrative_structure": "叙事结构",
  "tone": "表达风格",
  "rhythm": "节奏特征",
  "hook_rules": ["悬念规则"],
  "sentence_patterns": ["抽象句式结构，不含参考原句"],
  "originality_rules": ["原创约束"],
  "prompt_template": "可直接用于生成原创电影解说脚本的完整提示词"
}}

参考文本：
{sample_block}
""".strip()

    def parse_profile(self, response_text: str) -> ReferenceStyleProfile:
        payload = self.extract_json_object(response_text)
        return ReferenceStyleProfile(
            opening_type=str(payload.get("opening_type", "") or ""),
            narrative_structure=str(payload.get("narrative_structure", "") or ""),
            tone=str(payload.get("tone", "") or ""),
            rhythm=str(payload.get("rhythm", "") or ""),
            hook_rules=self.as_string_list(payload.get("hook_rules")),
            sentence_patterns=self.as_string_list(payload.get("sentence_patterns")),
            originality_rules=self.as_string_list(payload.get("originality_rules")) or self.default_originality_rules(),
            prompt_template=str(payload.get("prompt_template", "") or self.build_default_prompt_template()),
        )

    def build_default_prompt_template(self) -> str:
        return """
你是一名电影解说创作者。请根据我提供的电影剧情信息，生成一篇原创电影解说脚本。

创作要求：
1. 开头 15 秒内抛出核心冲突或悬念。
2. 用口语化表达推动剧情，不要机械复述情节。
3. 每 3 到 5 句话设置一个小悬念或信息推进。
4. 突出人物选择、因果关系和反转点。
5. 禁止复制、改写或模仿任何参考文案原句。
6. 输出必须是原创表达。

输出格式：
按分镜段落输出，每段包含：解说文案、画面建议、情绪标签、预计时长。
""".strip()

    def default_originality_rules(self) -> list[str]:
        return [
            "只学习结构、节奏和创作方法，不复用参考文案原句",
            "相同剧情信息必须重新组织语言表达",
            "避免固定套用某个创作者的标志性口头禅或独特表达",
        ]

    def extract_json_object(self, response_text: str) -> dict[str, Any]:
        cleaned = (response_text or "").strip()
        code_block = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
        if code_block:
            cleaned = code_block.group(1).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"参考样片分析结果不是合法 JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("参考样片分析结果必须是 JSON 对象")
        return parsed

    def as_string_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def truncate_text(self, text: str, max_length: int) -> str:
        cleaned = re.sub(r"\s+", " ", text or "").strip()
        if len(cleaned) <= max_length:
            return cleaned
        return cleaned[:max_length] + "..."
