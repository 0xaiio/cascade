"""解析链接：`POST /api/analyze`。

只有一条路由也要独立成文件：它的变化原因（请求体字段、解析错误到状态码的映射）与别处无关。
"""

from fastapi import APIRouter, HTTPException

from .. import api_support
from ..api_context import ContextDep
from ...contracts.schemas import AnalyzeRequest, AnalyzeResponse

router = APIRouter()


@router.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest, ctx: ContextDep) -> AnalyzeResponse:
    """解析元数据。失败一律 400。

    「需要登录 / bot 校验」不再触发浏览器导入 —— 只尝试一次，失败消息里带
    「怎么导出 cookies.txt」的下一步（见 `extract_metadata_with_cookies`）。
    """
    try:
        analysis = api_support.extract_metadata_with_cookies(
            ctx.service, ctx.settings, request.url, request.cookies_enabled
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 空间结论是解析结果的一部分：**在建任务之前**就让用户看到「这一批大概要占多少」。
    # 它只是提示；真正的阻断发生在 POST /api/jobs（那里才知道用户选了哪个清晰度）。
    return api_support.attach_analysis_disk_budget(analysis, ctx.settings)
