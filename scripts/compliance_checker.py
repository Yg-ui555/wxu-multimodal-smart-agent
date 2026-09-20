"""
合规检查脚本
用于检测项目中可能存在的违法违规操作
使用方法: python scripts/compliance_checker.py
"""

import os
import sys
import re

# 需要检测的敏感关键词
SENSITIVE_PATTERNS = {
    "未经授权的爬虫": [
        r"scrape.*without.*permission",
        r"爬.*未经",
        r"未经.*授权.*爬取",
    ],
    "版权侵权": [
        r"crack",
        r"pirate",
        r"盗版",
        r"破解.*版权",
    ],
    "恶意代码": [
        r"病毒",
        r"malware",
        r"ransomware",
        r"木.*马",
    ],
    "入侵工具": [
        r"exploit.*injection",
        r"sql.*injection",
        r"xss.*attack",
    ],
    "隐私泄露": [
        r"gather.*privacy",
        r"leak.*data",
        r"泄露.*个人信息",
    ],
    "虚假信息": [
        r"生成.*谣言",
        r"deepfake",
        r"伪造.*新闻",
    ],
}

# 需要检查的文件扩展名
CHECK_EXTENSIONS = [".py", ".txt", ".md", ".js", ".sh", ".bat"]


def check_file(filepath):
    """检查单个文件"""
    issues = []

    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        for category, patterns in SENSITIVE_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    issues.append({
                        "category": category,
                        "pattern": pattern,
                    })
                    break  # 每个类别只报一次
    except Exception as e:
        pass  # 忽略无法读取的文件

    return issues


def check_directory(root_dir):
    """检查整个目录"""
    all_issues = []

    for root, dirs, files in os.walk(root_dir):
        # 跳过不需要检查的目录（虚拟环境、数据、日志等大目录）
        dirs[:] = [d for d in dirs if d not in [".git", "venv", "venv311", "venv_old", "__pycache__", "node_modules", "data", "logs", "assets"]]

        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in CHECK_EXTENSIONS:
                filepath = os.path.join(root, file)
                issues = check_file(filepath)
                if issues:
                    for issue in issues:
                        issue["file"] = os.path.relpath(filepath, root_dir)
                    all_issues.extend(issues)

    return all_issues


def main():
    print("=" * 60)
    print("合规检查脚本")
    print("=" * 60)

    # 检查项目根目录
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print(f"\n检查目录: {project_dir}")
    print("正在扫描...")

    issues = check_directory(project_dir)

    if not issues:
        print("\n[OK] 未发现明显的违法违规操作")
    else:
        print(f"\n[WARN] 发现 {len(issues)} 个潜在问题:")
        print("-" * 60)

        # 按文件分组显示
        file_issues = {}
        for issue in issues:
            filepath = issue.pop("file")
            if filepath not in file_issues:
                file_issues[filepath] = []
            file_issues[filepath].append(issue)

        for filepath, file_issue_list in file_issues.items():
            print(f"\n[FILE] {filepath}")
            for issue in file_issue_list:
                print(f"   [!] {issue['category']} (匹配: {issue['pattern']})")

        print("\n" + "=" * 60)
        print("建议: 请确认以上操作是否符合法律法规和项目许可协议")
        print("如有问题，请咨询专业人士或项目负责人")
        print("=" * 60)


if __name__ == "__main__":
    main()
