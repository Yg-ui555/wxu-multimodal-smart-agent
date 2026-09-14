"""
Gradio 网页应用：单文本输入框，问答输出
启动命令：python src/web_app/app.py
"""

import os
import sys

# 将项目根目录添加到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import gradio as gr
from src.rag.vector_store import VectorStoreManager
from src.rag.retriever import create_retriever
from src.rag.llm_chain import create_qa_system


class RAGApp:
    """RAG 问答应用"""

    def __init__(self):
        """初始化应用"""
        self.vectorstore = None
        self.retriever = None
        self.qa_system = None
        self.docs_dir = "data/docs"

    def initialize(self):
        """初始化向量库和问答系统"""
        print("正在初始化...")

        # 初始化向量库
        manager = VectorStoreManager(persist_directory="data/faiss_db")

        # 尝试加载已存在的向量库
        try:
            self.vectorstore = manager.load_vectorstore()
            print("向量库加载成功")
        except FileNotFoundError:
            print("向量库不存在，请先运行 build_vectorstore.py 构建向量库")
            return False

        # 创建检索器
        self.retriever = create_retriever(self.vectorstore, top_k=4)
        print("检索器创建成功")

        # 创建问答系统
        try:
            self.qa_system = create_qa_system(self.retriever)
            print("问答系统创建成功")
        except ValueError as e:
            print(f"LLM 初始化失败: {e}")
            return False

        return True

    def answer_question(self, question: str) -> tuple:
        """
        回答问题
        Args:
            question: 用户问题
        Returns:
            (回答内容, 相关文档)
        """
        if not question or not question.strip():
            return "请输入问题", ""

        try:
            result = self.qa_system.answer(question)
            answer = result["answer"]

            # 获取相关文档列表
            docs_text = ""
            if "sources" in result:
                # 去重收集文件名
                files = set()
                for src in result["sources"]:
                    source = src.get("source", "")
                    if source:
                        files.add(source)

                if files:
                    docs_text = "**相关文档：**\n\n"
                    for f in files:
                        # 构建相对路径
                        file_path = os.path.join(self.docs_dir, f)
                        if os.path.exists(file_path):
                            # 显示文件信息
                            docs_text += f"📄 [{f}]({file_path})\n\n"

            return answer, docs_text

        except Exception as e:
            return f"发生错误: {str(e)}", ""


def build_gradio_interface(app: RAGApp) -> gr.Blocks:
    """
    构建 Gradio 界面
    Args:
        app: RAG 应用实例
    Returns:
        Gradio Blocks
    """
    with gr.Blocks(title="无锡学院智能助手", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# 无锡学院智能助手")
        gr.Markdown("基于 RAG 的校园知识问答系统")

        with gr.Row():
            with gr.Column(scale=3):
                question_input = gr.Textbox(
                    label="请输入您的问题",
                    placeholder="例如：大一上学期有哪些课程？",
                    lines=2
                )
                submit_btn = gr.Button("提交问题", variant="primary")

            with gr.Column(scale=7):
                answer_output = gr.Markdown(
                    label="回答"
                )

        gr.Markdown("### 📚 相关文档")
        docs_output = gr.Markdown(
            label="相关文档"
        )

        # 绑定事件
        submit_btn.click(
            fn=app.answer_question,
            inputs=[question_input],
            outputs=[answer_output, docs_output]
        )

        # 回车提交
        question_input.submit(
            fn=app.answer_question,
            inputs=[question_input],
            outputs=[answer_output, docs_output]
        )

        gr.Markdown("---")
        gr.Markdown("💡 **提示**：相关文档点击后可直接打开查看")

    return demo


def main():
    """主函数"""
    print("=" * 50)
    print("无锡学院智能助手 - RAG 问答系统")
    print("=" * 50)

    # 创建应用
    app = RAGApp()

    # 初始化
    if not app.initialize():
        print("\n初始化失败，请检查配置后重试")
        print("提示：")
        print("1. 确保 .env 文件中配置了有效的 LLM_API_KEY")
        print("2. 确保 data/faiss_db 目录下已有向量库数据")
        print("3. 如需重新构建向量库，请运行 build_vectorstore.py")
        return

    # 构建界面
    print("\n正在启动 Gradio 界面...")
    demo = build_gradio_interface(app)

    # 启动服务
    demo.launch(
        server_name="0.0.0.0",
        server_port=7861,
        share=False,
        inbrowser=True
    )


if __name__ == "__main__":
    main()
