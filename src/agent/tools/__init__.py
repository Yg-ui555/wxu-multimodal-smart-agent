"""4 个真 tool:search_school_notices / get_user_schedule / ocr_image / web_search"""
from src.agent.tools.search_school_notices import search_school_notices
from src.agent.tools.get_user_schedule import get_user_schedule
from src.agent.tools.ocr_image import ocr_image
from src.agent.tools.web_search import web_search

ALL_TOOLS = [
    search_school_notices,
    get_user_schedule,
    ocr_image,
    web_search,
]
TOOL_MAP = {t.name: t for t in ALL_TOOLS}
