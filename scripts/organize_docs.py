"""
文档分类脚本
根据文件名自动将文档分类到不同文件夹
"""

import os
import shutil
import re

# 分类规则：关键词 -> 文件夹名
CATEGORIES = {
    "高等数学": ["高数", "高等数学", "微积分", "微分方程"],
    "线性代数": ["线性代数", "矩阵", "行列式", "向量"],
    "复变函数": ["复变", "积分变换", "拉普拉斯", "傅里叶"],
    "概率论": ["概率", "随机过程", "数理统计"],
    "信号与系统": ["信号与系统", "信号系统"],
    "电路": ["电路", "电路分析"],
    "模拟电子技术": ["模拟电路", "模电", "放大电路", "运放"],
    "数字电子技术": ["数字电路", "数电", "逻辑门", "触发器", "计数器"],
    "单片机/微机": ["单片机", "微机原理", "单片机实验", "微机实验", "C51"],
    "FPGA": ["FPGA", "Verilog", "VHDL"],
    "C语言": ["C语言", "C++", "程序设计", "编程题"],
    "数据结构与算法": ["数据结构", "算法", "算法概念"],
    "Python/AI": ["Python", "深度学习", "人工智能", "YOLO", "机器学习", "神经网络"],
    "英语": ["英语", "CET", "四级", "六级", "IELTS", "雅思", "TOEFL"],
    "物理": ["物理", "力学", "电磁学", "光学"],
    "马克思主义": ["马克思主义", "毛概", "习思想", "思政", "近代史"],
    "创新创业": ["创新创业", "三创赛", "互联网+", "挑战杯", "商业计划书"],
    "实验报告": ["实验报告", "实验一", "实验二", "实验三", "实验四"],
    "期末试卷": ["期末试卷", "期末考试", "A卷", "B卷", "答案"],
    "学校通知": ["通知", "公示", "报名表", "须知", "指南"],
    "实践/实习": ["金工实习", "生产实习", "毕业实习", "实践报告"],
    "其他": []
}

def get_category(filename):
    """根据文件名判断类别"""
    filename_lower = filename.lower()

    for category, keywords in CATEGORIES.items():
        if category == "其他":
            continue
        for keyword in keywords:
            if keyword.lower() in filename_lower:
                return category

    return "其他"

def main():
    raw_dir = "D:/wxu/wxu-multimodal-smart-agent/data/docs/raw"
    base_dir = "D:/wxu/wxu-multimodal-smart-agent/data/docs"

    # 创建分类文件夹
    for category in CATEGORIES.keys():
        category_dir = os.path.join(base_dir, category)
        if not os.path.exists(category_dir):
            os.makedirs(category_dir)
            print(f"创建目录: {category}")

    # 获取所有文件
    files = [f for f in os.listdir(raw_dir) if os.path.isfile(os.path.join(raw_dir, f))]

    # 统计
    stats = {}
    moved_files = []

    # 移动文件
    for filename in files:
        category = get_category(filename)
        src = os.path.join(raw_dir, filename)
        dst = os.path.join(base_dir, category, filename)

        # 如果目标文件已存在，添加数字后缀
        if os.path.exists(dst):
            name, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(dst):
                dst = os.path.join(base_dir, category, f"{name}_{counter}{ext}")
                counter += 1

        shutil.move(src, dst)
        moved_files.append((filename, category))

        if category not in stats:
            stats[category] = 0
        stats[category] += 1

    # 打印统计结果
    print("\n" + "=" * 50)
    print("分类完成！统计如下：")
    print("=" * 50)
    for category, count in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"{category}: {count} 个文件")

    print(f"\n总计: {len(files)} 个文件")

    # 保存分类结果到文件
    with open(os.path.join(base_dir, "分类结果.txt"), "w", encoding="utf-8") as f:
        f.write("文件分类结果\n")
        f.write("=" * 50 + "\n")
        for category, count in sorted(stats.items(), key=lambda x: -x[1]):
            f.write(f"{category}: {count} 个文件\n")

        f.write("\n详细列表:\n")
        for filename, category in moved_files:
            f.write(f"{filename} -> {category}\n")

if __name__ == "__main__":
    main()
