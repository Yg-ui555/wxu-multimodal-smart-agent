"""
LLM 问答链：拼接 prompt + 检索片段，调用 LLM 生成答案
"""

import os
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
from langchain.chat_models import ChatOpenAI
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from langchain.schema import Document

# 加载 .env 文件（处理 BOM 头）
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
load_dotenv(env_path, encoding='utf-8-sig')


# 默认 Prompt 模板（中文）
DEFAULT_TEMPLATE = """你是一个简洁的智能校园助手，专门帮助无锡学院的学生解答问题。

请根据以下参考文档内容回答用户的问题。如果参考内容没有相关信息，请如实告知用户。

参考文档内容：
{context}

用户问题：{question}

要求：
1. 只根据提供的参考文档内容回答，不要编造信息
2. 回答要简洁明了，直接给出答案，不需要解释文档内容
3. 如需列表，最多只列3-5条关键信息
4. 如果没有找到相关信息，请说"抱歉，资料库中没有找到相关信息"

回答："""

# 带引用来源的 Prompt 模板
CITATION_TEMPLATE = """你是一个智能校园助手，专门帮助无锡学院的学生解答问题。

请根据以下参考文档内容回答用户的问题。如果参考内容没有相关信息，请如实告知用户。

参考文档内容：
{context}

用户问题：{question}

要求：
1. 只根据提供的参考文档内容回答，不要编造信息
2. 回答要清晰、准确、简洁
3. 如果没有找到相关信息，请明确说明"根据我目前掌握的资料，没有找到相关信息"
4. 在回答中标注参考来源的编号

回答（附引用来源）："""


class LLMQASystem:
    """LLM 问答系统"""

    def __init__(
        self,
        retriever,
        model_name: str = "gpt-3.5-turbo",
        temperature: float = 0.3,
        template: str = None
    ):
        """
        初始化 LLM 问答系统
        Args:
            retriever: 检索器实例
            model_name: LLM 模型名称
            temperature: 温度参数
            template: Prompt 模板
        """
        self.retriever = retriever
        self.temperature = temperature
        self.template = template or DEFAULT_TEMPLATE

        # 初始化 LLM
        self.llm = self._init_llm(model_name)

        # 创建 Prompt
        self.prompt = PromptTemplate(
            template=self.template,
            input_variables=["context", "question"]
        )

        # 创建问答链
        self.chain = LLMChain(llm=self.llm, prompt=self.prompt)

    def _init_llm(self, model_name: str):
        """初始化 LLM"""
        # 从环境变量读取 API 配置
        api_key = os.getenv("LLM_API_KEY", "")
        base_url = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
        # 支持自定义模型，默认使用 deepseek-chat
        model = os.getenv("LLM_MODEL", model_name)

        if not api_key:
            raise ValueError("请在 .env 文件中配置有效的 LLM_API_KEY")

        return ChatOpenAI(
            model=model,
            temperature=self.temperature,
            openai_api_key=api_key,
            openai_api_base=base_url
        )

    def answer(
        self,
        question: str,
        return_sources: bool = True
    ) -> Dict[str, Any]:
        """
        回答用户问题
        Args:
            question: 用户问题
            return_sources: 是否返回来源
        Returns:
            {"answer": 回答内容, "sources": 来源列表}
        """
        # 1. 检索相关文档
        context, sources = self.retriever.retrieve_with_sources(question)

        # 2. 调用 LLM 生成答案
        response = self.chain.run(context=context, question=question)

        result = {
            "answer": response.strip(),
            "context": context
        }

        if return_sources:
            result["sources"] = sources

        return result

    def answer_simple(self, question: str) -> str:
        """
        简单回答（不带来源）
        Args:
            question: 问题
        Returns:
            回答内容
        """
        return self.answer(question, return_sources=False)["answer"]


def create_qa_system(retriever, model_name: str = "gpt-3.5-turbo") -> LLMQASystem:
    """
    便捷函数：创建问答系统
    Args:
        retriever: 检索器
        model_name: 模型名称
    Returns:
        LLMQASystem 实例
    """
    return LLMQASystem(retriever, model_name)


if __name__ == "__main__":
    # 测试
    from vector_store import VectorStoreManager
    from retriever import create_retriever

    print("加载向量库...")
    manager = VectorStoreManager(persist_directory="data/chroma_db")
    vs = manager.load_vectorstore()

    print("创建检索器...")
    retriever = create_retriever(vs, top_k=3)

    print("创建问答系统...")
    qa = create_qa_system(retriever)

    print("\n测试问答...")
    result = qa.answer("电子信息工程专业需要修满多少学分？")

    print("\n=== 回答 ===")
    print(result["answer"])

    if "sources" in result:
        print("\n=== 来源 ===")
        for src in result["sources"]:
            print(f"[{src['index']}] {src['source']} (页 {src['page']})")
