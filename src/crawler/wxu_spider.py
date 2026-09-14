"""
无锡学院官网爬虫 - 增强版
支持多页面爬取和PDF下载
"""

import os
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re


class WXUSpider:
    """无锡学院官网爬虫"""

    def __init__(self):
        self.base_url = "https://www.cwxu.edu.cn"
        self.jwc_url = "http://jwc.cwxu.edu.cn"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        self.pdf_links = []

    def get_soup(self, url):
        """获取页面BeautifulSoup对象"""
        try:
            response = self.session.get(url, timeout=10)
            response.encoding = 'utf-8'
            return BeautifulSoup(response.text, "html.parser")
        except Exception as e:
            print(f"获取页面失败: {url} - {e}")
            return None

    def find_all_pdf_links(self, base_url):
        """查找页面及子链接中的所有PDF"""
        print(f"\n{'='*50}")
        print(f"正在爬取: {base_url}")
        print(f"{'='*50}")

        # 1. 直接访问页面找PDF
        soup = self.get_soup(base_url)
        if soup:
            self._extract_pdf_from_soup(soup, base_url)

        # 2. 访问通知列表页
        notice_pages = [
            f"{self.jwc_url}/index/tzgg.htm",  # 教务处通知公告
            f"{self.jwc_url}/gs.htm",  # 公示
            f"{self.jwc_url}/gzzd/xnwj.htm",  # 校内文件
        ]

        for page in notice_pages:
            soup = self.get_soup(page)
            if soup:
                # 提取列表页中的通知链接
                links = soup.find_all('a', href=True)
                for a in links:
                    href = a.get('href', '')
                    text = a.get_text(strip=True)

                    # 处理相对路径
                    if href.startswith('../'):
                        href = href.replace('..', self.jwc_url)
                    elif not href.startswith('http'):
                        href = urljoin(self.jwc_url, href)

                    # 访问通知详情页
                    if 'info' in href and '.htm' in href:
                        detail_soup = self.get_soup(href)
                        if detail_soup:
                            self._extract_pdf_from_soup(detail_soup, href)

            # 安全间隔
            time.sleep(2)

    def _extract_pdf_from_soup(self, soup, base_url):
        """从页面中提取PDF链接"""
        if not soup:
            return

        for a in soup.find_all('a', href=True):
            href = a.get('href', '')
            text = a.get_text(strip=True)

            # 处理相对路径
            if not href.startswith('http'):
                href = urljoin(base_url, href)

            # 检查是否是PDF
            if '.pdf' in href.lower():
                # 去除查询参数
                pdf_url = href.split('?')[0]
                if pdf_url not in [p['url'] for p in self.pdf_links]:
                    self.pdf_links.append({
                        'name': text or "未命名",
                        'url': pdf_url,
                        'source': base_url
                    })
                    print(f"找到PDF: {text[:30]} -> {pdf_url[:60]}")

    def save_pdf_links(self, save_path="data/docs/crawled"):
        """保存PDF链接到文件"""
        os.makedirs(save_path, exist_ok=True)

        with open(f"{save_path}/pdf_links.txt", "w", encoding="utf-8") as f:
            f.write(f"# 无锡学院 PDF文件列表\n")
            f.write(f"# 爬取时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# 总数: {len(self.pdf_links)}\n\n")

            for i, pdf in enumerate(self.pdf_links, 1):
                f.write(f"{i}. {pdf['name']}\n")
                f.write(f"   URL: {pdf['url']}\n")
                f.write(f"   来源: {pdf['source']}\n\n")

        print(f"\n已保存 {len(self.pdf_links)} 个PDF链接到: {save_path}/pdf_links.txt")

    def download_pdf(self, pdf_url, save_dir="data/docs/crawled/pdfs"):
        """下载单个PDF"""
        os.makedirs(save_dir, exist_ok=True)

        filename = pdf_url.split('/')[-1].split('?')[0]
        if not filename.endswith('.pdf'):
            filename += '.pdf'

        filepath = os.path.join(save_dir, filename)

        try:
            response = self.session.get(pdf_url, timeout=30)
            with open(filepath, 'wb') as f:
                f.write(response.content)
            print(f"下载成功: {filepath}")
            return filepath
        except Exception as e:
            print(f"下载失败: {pdf_url} - {e}")
            return None


def main():
    """主函数"""
    spider = WXUSpider()

    print("开始爬取无锡学院官网...")
    print("目标: 教务处通知公告 PDF文件")

    # 爬取
    spider.find_all_pdf_links(spider.jwc_url)

    # 保存链接
    spider.save_pdf_links()

    print(f"\n爬取完成! 共找到 {len(spider.pdf_links)} 个PDF文件")


if __name__ == "__main__":
    main()
