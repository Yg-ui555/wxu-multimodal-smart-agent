"""
Locust 压测脚本（2026-10-12 新增）
==================================

# ============================================================
# 📚 本文件重点掌握能力
# ============================================================
# 1. Locust HttpUser 编写:每个虚拟用户模拟一个客户端
# 2. wait_time 控制 RPS(频率)
# 3. @task 装饰器标注具体行为
# 4. 任务权重:数字 = 该任务被选中的相对概率
# 5. on_start 钩子:用户启动时执行一次(比如登录)
#
# 💡 面试高频问题：
#    - "压测关注哪些指标？" → QPS / p50/p90/p99 延迟 / 错误率 / 吞吐量
#    - "怎么模拟真实场景？" → 权重分布贴近真实流量(80% 问 RAG + 20% 看课表)
#    - "locust 和 wrk/jmeter 区别？" → locust 纯 Python 易扩展,wrk 压测上限高
#
# 📖 完整能力地图：docs/LEARNING_GUIDE.md
# ============================================================

用法:
    # 1) 启动被压测服务(另一个终端):
    powershell -ExecutionPolicy Bypass -File start.ps1
    # 或手动:
    venv311\Scripts\python.exe src\web_app\fastapi_app.py

    # 2) 启动 locust(本脚本所在目录):
    locust -f tests/locustfile.py --host=http://localhost:7862

    # 3) 打开 http://localhost:8089 配置(50 用户, 5/秒启动, 跑 5 分钟)
    # 或无 UI 模式:
    locust -f tests/locustfile.py --host=http://localhost:7862 -u 50 -r 5 -t 5m --html tests/load_test_report.html
"""
import json
import random
import time
from locust import HttpUser, task, between, events


# ============================================================
# 测试 query 池(从 eval_set.json 抽 20 道,模拟真实校园问答流量)
# ============================================================
EVAL_QUERIES = [
    # 政策类(40%)
    "校一等奖学金多少钱?",
    "国家助学金和校奖学金能同时拿吗?",
    "奖学金评定主要看什么?",
    "转专业什么时候申请?",
    "转专业要准备什么材料?",
    "转专业对成绩有什么要求?",
    "四级什么时候报名?",
    "四级报名费多少?",

    # 课程/培养方案(20%)
    "电子信息工程专业必修课有哪些?",
    "门电路是什么?",
    "组合逻辑电路相关知识",
    "一节课多长时间?",

    # 图书馆/校园(20%)
    "图书馆几点关门?",
    "图书馆周末开放吗?周一闭馆吗?",
    "图书馆可以借几本书?",

    # 闲聊(10%)
    "你好",
    "你是谁?",

    # 失败兜底(10%)
    "今天无锡天气怎么样?",
    "Python 怎么入门?",
]


class CampusQAUser(HttpUser):
    """
    模拟一个校园问答用户
    行为权重:80% 问 RAG,15% 看课表,5% 上传
    """
    # 每次请求间隔 1-3 秒(模拟真人思考时间)
    wait_time = between(1, 3)

    def on_start(self):
        """用户启动时执行一次:确保 cookie 有 uid(触发 _ensure_uid)"""
        # 访问首页建立 cookie
        self.client.get("/", name="[setup] GET /")

    # ============== 主要任务:发 chat ==============
    @task(80)
    def chat_query(self):
        """80% 概率:发 chat 请求(走 RAG 路径)"""
        query = random.choice(EVAL_QUERIES)
        # /api/chat 接受 POST JSON
        with self.client.post(
            "/api/chat",
            json={"query": query},
            name="POST /api/chat (RAG)",
            catch_response=True,
            stream=True,  # SSE 流式
        ) as resp:
            # 流式响应:只检查首字节时间和状态码
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"status={resp.status_code}")

    # ============== 次要任务:看课表 ==============
    @task(10)
    def view_schedule(self):
        """10% 概率:看课表"""
        with self.client.get("/api/schedule", name="GET /api/schedule", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code}")
            else:
                resp.success()

    # ============== 监控任务:抓 metrics ==============
    @task(3)
    def fetch_metrics(self):
        """3% 概率:抓 Prometheus 指标(模拟 Prometheus 抓取)"""
        with self.client.get("/metrics", name="GET /metrics", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code}")
            else:
                resp.success()

    # ============== 健康检查 ==============
    @task(2)
    def health_check(self):
        """2% 概率:健康检查"""
        with self.client.get("/health", name="GET /health", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code}")
            else:
                resp.success()


# ============================================================
# 事件钩子(可选,用来收集自定义指标)
# ============================================================
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n" + "=" * 60)
    print("🚀 压测开始")
    print("=" * 60)
    print(f"目标 host: {environment.host}")
    print(f"用户数: {environment.runner.target_user_count if hasattr(environment.runner, 'target_user_count') else 'N/A'}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    print("\n" + "=" * 60)
    print("✅ 压测结束")
    print("=" * 60)
    stats = environment.stats
    print(f"  总请求:    {stats.total.num_requests}")
    print(f"  失败数:    {stats.total.num_failures}")
    print(f"  失败率:    {stats.total.fail_ratio * 100:.2f}%")
    print(f"  RPS:       {stats.total.total_rps:.2f}")
    print(f"  p50 延迟:  {stats.total.get_response_time_percentile(0.5):.0f}ms")
    print(f"  p90 延迟:  {stats.total.get_response_time_percentile(0.9):.0f}ms")
    print(f"  p99 延迟:  {stats.total.get_response_time_percentile(0.99):.0f}ms")
    print(f"  平均延迟:  {stats.total.avg_response_time:.0f}ms")
    print(f"  最大延迟:  {stats.total.max_response_time:.0f}ms")
