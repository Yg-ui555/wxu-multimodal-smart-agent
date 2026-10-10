# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. LangChain 文档加载器（PyMuPDFLoader / Docx2txtLoader / TextLoader）
# 2. PDF 乱码清洗（GBK 字节被 UTF-8 解释后的乱码字符识别与替换）
# 3. 标题感知分块（先按"第X章/一、"切分章节，再在章节内按 chunk_size 切）
# 4. 结构化 metadata（date / category / section / content_hash）
# 5. 内容哈希去重（source + hash 去重，清理爬虫重复入库）
# 6. 中英文混合文档的分块边界处理
#
# 💡 面试高频问题：
#    - "为什么 chunk_size=800？" → 经验值，召回精度和上下文长度的平衡
#    - "为什么 overlap=150？" → 防止关键信息被切到两块丢失
#    - "扫描版 PDF 怎么办？" → 需 OCR，本项目用 PyMuPDF 提取文字，扫描版会得到 0 块
#    - "如何避免主题被切散？" → 标题感知分块：先识别章节边界，章节内再切
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

"""
文档加载器：读取 PDF、TXT，清洗换行、多余空格，文本分块
参考 LangChain 官方文档: https://python.langchain.com/docs/tutorials/rag/
"""

import os
import re
import sys
import hashlib
from typing import List

# 解决Windows控制台编码问题
# 2026-10-09 修复:daily_crawl.py 子进程里 sys.stdout 可能是 None,直接 reconfigure 会 AttributeError 炸掉
# 加 sys.stdout is not None 守卫 + try/except 兜底
if sys.platform == "win32" and sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass
if sys.platform == "win32" and sys.stderr is not None:
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except (AttributeError, OSError):
        pass
from langchain_community.document_loaders import PyMuPDFLoader, TextLoader, Docx2txtLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document


# 标题识别正则（中文章节标题）
HEADING_PATTERNS = [
    re.compile(r'^第[一二三四五六七八九十百零\d]+章\b'),       # 第一章、第1章
    re.compile(r'^第[一二三四五六七八九十\d]+[节条]\b'),         # 第一节、第1条
    re.compile(r'^[一二三四五六七八九十]+、'),                  # 一、二、
    re.compile(r'^\d+(\.\d+)*\s+\S'),                           # 1.1 xxx, 1 xxx
    re.compile(r'^[（(][一二三四五六七八九十\d]+[)）]\s*\S'),    # （一）xxx
]


def _is_heading(line: str) -> bool:
    """判断一行是否是标题"""
    line = line.strip()
    if not line or len(line) > 50:
        return False
    for pat in HEADING_PATTERNS:
        if pat.match(line):
            return True
    return False


def _extract_date(text: str, file_name: str = "") -> str:
    """
    从文本或文件名中提取日期（年份优先）
    返回格式: YYYY 或 YYYY-MM 或空字符串
    """
    # 先从文件名提取（如 "2025年招生简章.pdf"）
    year_match = re.search(r'(20\d{2})', file_name)
    if year_match:
        return year_match.group(1)
    # 从文本前 500 字提取
    sample = text[:500]
    # 匹配 "2025年3月" 或 "2025-03" 或 "2025.03"
    m = re.search(r'(20\d{2})[年.\-](\d{1,2})', sample)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}"
    m = re.search(r'(20\d{2})', sample)
    if m:
        return m.group(1)
    return ""


def _extract_category(source_path: str) -> str:
    """
    从相对路径提取文档类别（取第一级目录名）
    如 "学校通知/2025/xxx.pdf" → "学校通知"
    """
    parts = source_path.replace("\\", "/").split("/")
    if len(parts) > 1:
        return parts[0]
    return "未分类"


def _content_hash(text: str) -> str:
    """计算内容的 MD5 哈希（用于去重）"""
    # 归一化后再哈希：去除空白和标点差异
    normalized = re.sub(r'\s+', '', text)
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()[:16]


def dedup_chunks(chunks: List[Document]) -> List[Document]:
    """
    基于 source + content_hash 去重
    保留首次出现的 chunk
    """
    seen = set()
    result = []
    for chunk in chunks:
        source = chunk.metadata.get("source", "")
        h = chunk.metadata.get("content_hash", "")
        key = f"{source}|{h}"
        if key in seen:
            continue
        seen.add(key)
        result.append(chunk)
    return result


class DocumentLoader:
    """文档加载器，支持 PDF 和 TXT"""

    def __init__(self, docs_dir: str = "data/docs"):
        """
        初始化文档加载器
        Args:
            docs_dir: 文档目录路径
        """
        self.docs_dir = docs_dir

    def load_document(self, file_path: str) -> List[Document]:
        """
        加载单个文档
        Args:
            file_path: 文档路径
        Returns:
            Document 列表
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            loader = PyMuPDFLoader(file_path)
        elif ext == ".txt":
            loader = TextLoader(file_path, encoding="utf-8")
        elif ext == ".md":
            # Markdown 当文本读（2026-09-17 新增：用户上传 Markdown 时支持）
            loader = TextLoader(file_path, encoding="utf-8")
        elif ext in [".docx", ".doc"]:
            loader = Docx2txtLoader(file_path)
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

        docs = loader.load()
        # 添加更多metadata
        for doc in docs:
            doc.metadata["file_name"] = os.path.basename(file_path)
        return docs

    def clean_text(self, text: str) -> str:
        """
        清洗文本：去除多余换行、空格、特殊符号
        Args:
            text: 原始文本
        Returns:
            清洗后的文本
        """
        # 去除多余换行（保留段落分隔）
        text = re.sub(r'\n{3,}', '\n\n', text)
        # 去除行首行尾空格
        text = re.sub(r'^[ \t]+|[ \t]+$', '', text, flags=re.MULTILINE)
        # 去除多余空格
        text = re.sub(r' {2,}', ' ', text)

        # ========== 新增：去除PDF特殊符号 ==========
        # 去除常见的PDF乱码符号
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)  # 控制字符
        text = re.sub(r'‐‑‑{2,}', '—', text)  # 多个横线统一
        text = re.sub(r'[▪▫●○◆◇■□]', '', text)  # PDF常见装饰符号
        # 修复 HIGH #6（2026-09-17）：旧版 `re.sub(r'[ -  ]', ' ', text)`
        # 字符类里只有 3 个字面 ASCII 字符（空格 - 空格），完全无法清理
        # Unicode 空白（EN SPACE / EM SPACE / 全角空格等）。改用 explicit codepoints。
        text = re.sub(r'[\u00A0\u2000-\u200A\u202F\u205F\u3000]', ' ', text)  # Unicode 空白 → ASCII 空格
        text = re.sub(r'[•‣⁃⁄∙√∞]', '', text)  # 项目符号等

        # 去除连续的特殊符号（保留中文标点和基本英文）
        text = re.sub(r'[*#@%&+=|<>~`^]{2,}', '', text)

        # 修复常见PDF编码问题
        # LOW #4（2026-09-17 review）：text 此时是 str，
        # '\xfd'/'\xfe'/'\x0c' 实际分别是 U+00FD(ý)、U+00FE(þ)、U+000C(form feed)。
        # 中文 PDF 中这三个字符罕现，删除成本接近 0，所以保留现有行为。
        # 若改用 bytes 替换更精准，建议改在 PyMuPDFLoader 加载阶段而不是这里。
        text = text.replace('\xfd', '').replace('\xfe', '').replace('\x0c', '')

        # 去除首尾特殊符号
        text = re.sub(r'^[^\w一-鿿]+|[^\w一-鿿]+$', '', text)

        return text

    def load_all_documents(self, recursive: bool = True) -> List[Document]:
        """
        加载目录下所有文档
        Args:
            recursive: 是否递归子文件夹
        Returns:
            所有 Document 列表
        """
        if not os.path.exists(self.docs_dir):
            print(f"警告: 文档目录不存在: {self.docs_dir}")
            return []

        documents = []

        if recursive:
            # 递归遍历所有子文件夹
            for root, dirs, files in os.walk(self.docs_dir):
                for filename in files:
                    file_path = os.path.join(root, filename)
                    if os.path.isfile(file_path):
                        try:
                            docs = self.load_document(file_path)
                            # 清洗每个文档的文本内容
                            for doc in docs:
                                doc.page_content = self.clean_text(doc.page_content)
                                # 记录来源路径
                                rel_path = os.path.relpath(file_path, self.docs_dir)
                                doc.metadata["source"] = rel_path
                            documents.extend(docs)
                            print(f"已加载: {filename} ({len(docs)} 页)", flush=True, file=sys.stdout)
                        except Exception as e:
                            # 记录失败的文件，但不中断
                            print(f"跳过 (加载失败): {filename} - {str(e)[:50]}")
        else:
            # 只读取当前目录
            for filename in os.listdir(self.docs_dir):
                file_path = os.path.join(self.docs_dir, filename)
                if os.path.isfile(file_path):
                    try:
                        docs = self.load_document(file_path)
                        for doc in docs:
                            doc.page_content = self.clean_text(doc.page_content)
                        documents.extend(docs)
                        print(f"已加载: {filename} ({len(docs)} 页)")
                    except Exception as e:
                        print(f"加载失败 {filename}: {e}")

        return documents


class TextChunker:
    """文本分块器（标题感知）"""

    def __init__(self, chunk_size: int = 800, overlap: int = 150):
        """
        初始化分块器
        Args:
            chunk_size: 块大小
            overlap: 重叠大小
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            separators=["\n\n", "\n", "。", "！", "？", ".", "!", "?", " ", ""]
        )

    def _split_by_headings(self, text: str) -> List[tuple]:
        """
        按标题切分文本，返回 [(section_title, section_text), ...]
        如果没有标题，返回 [("", text)]
        """
        lines = text.split('\n')
        sections = []
        current_title = ""
        current_lines = []

        for line in lines:
            if _is_heading(line):
                # 保存上一段
                if current_lines:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                current_title = line.strip()
                current_lines = [line]
            else:
                current_lines.append(line)

        # 最后一段
        if current_lines:
            sections.append((current_title, "\n".join(current_lines).strip()))

        return sections

    def split_documents(self, documents: List[Document]) -> List[Document]:
        """
        将文档列表分块（标题感知 + metadata 补全）
        Args:
            documents: Document 列表
        Returns:
            分块后的 Document 列表
        """
        chunks = []
        for doc in documents:
            source = doc.metadata.get("source", "")
            file_name = doc.metadata.get("file_name", "")

            # 提取日期和类别
            date = _extract_date(doc.page_content, file_name)
            category = _extract_category(source)

            # 标题感知：先按章节切分
            sections = self._split_by_headings(doc.page_content)

            for section_title, section_text in sections:
                if not section_text.strip():
                    continue

                # 章节内再按 chunk_size 切分
                sub_chunks = self.splitter.split_text(section_text)

                for sub_text in sub_chunks:
                    # chunk 前缀加"【文档名/章节】"
                    prefix_parts = []
                    if file_name:
                        prefix_parts.append(file_name)
                    if section_title:
                        prefix_parts.append(section_title)
                    prefix = f"【{' / '.join(prefix_parts)}】" if prefix_parts else ""

                    full_text = f"{prefix}\n{sub_text}" if prefix else sub_text

                    new_doc = Document(
                        page_content=full_text,
                        metadata={
                            **doc.metadata,
                            "section": section_title,
                            "date": date,
                            "category": category,
                            "content_hash": _content_hash(sub_text),
                        }
                    )
                    chunks.append(new_doc)

        return chunks

    def split_text(self, text: str) -> List[str]:
        """
        将文本分块
        Args:
            text: 原始文本
        Returns:
            文本块列表
        """
        sections = self._split_by_headings(text)
        result = []
        for _, section_text in sections:
            result.extend(self.splitter.split_text(section_text))
        return result


def load_and_chunk(docs_dir: str = "data/docs", dedup: bool = True) -> List[Document]:
    """
    便捷函数：加载目录中所有文档并分块（含去重）
    Args:
        docs_dir: 文档目录
        dedup: 是否去重（默认 True）
    Returns:
        分块后的文档列表
    """
    loader = DocumentLoader(docs_dir)
    documents = loader.load_all_documents()

    if not documents:
        print("警告: 未加载到任何文档")
        return []

    chunker = TextChunker(chunk_size=800, overlap=150)
    chunks = chunker.split_documents(documents)

    before = len(chunks)
    if dedup:
        chunks = dedup_chunks(chunks)
    after = len(chunks)

    print(f"文档分块完成: 共 {after} 个文本块" + (f"（去重: {before} → {after}）" if dedup else ""))
    return chunks


if __name__ == "__main__":
    # 测试
    chunks = load_and_chunk()
    if chunks:
        print(f"\n示例文本块 (前500字符):")
        print(chunks[0].page_content[:500])
        print(f"\nmetadata 示例:")
        print(chunks[0].metadata)
