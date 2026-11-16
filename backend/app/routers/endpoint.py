"""接口管理接口：维护API端点，覆盖接入登记、提交校验、激活端点、停用端点等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.endpoint import EndpointService

router = APIRouter(prefix="/api/endpoint", tags=["接口管理"])

service = EndpointService()

LIST_FIELDS = ["端点编号", "接口地址", "请求方法", "认证方式", "请求头配置", "参数模板", "响应格式", "超时秒数"]
STATUSES = ["草稿", "待校验", "已激活", "已停用"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按端点编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已激活、已停用"),
    address: str | None = Query(default=None, description="按接口地址检索（支持规范化地址匹配）"),
    method: str | None = Query(default=None, description="按请求方法过滤"),
    onboard_status: str | None = Query(default=None, alias="onboardStatus", description="按接入状态过滤"),
    executable: bool | None = Query(default=None, description="只返回可执行清单，排除被驳回/隔离项"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按端点编号、接口地址与状态过滤接口管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        address=address,
        method=method,
        onboard_status=onboard_status,
        executable=executable,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/quarantine", response_model=PageResult[dict])
def list_quarantine(
    address: str | None = Query(default=None, description="按接口地址过滤隔离记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """隔离区：被拒绝的接入登记，完整请求头配置仍可回看。"""
    items, total = service.list_quarantine(address=address, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/receipts", response_model=PageResult[dict])
def list_receipts(
    code: str | None = Query(default=None, description="按端点编号检索回执"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """接入回执：按端点编号关联，成功与拒绝都留痕。"""
    items, total = service.list_receipts(code=code, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/stats/summary")
def onboard_stats() -> dict[str, int]:
    """接口管理待处理区接入统计：总数、已接入、待接入、已驳回、隔离数。"""
    return service.onboard_stats()


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出接口管理清单：返回当前过滤条件下的全量数据（不含被隔离项）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "endpoint", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条API端点明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"API端点 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条API端点，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="API端点已登记", entry=entry)


@router.post("/registration", response_model=ActionResult)
def save_registration(payload: EntryPayload) -> ActionResult:
    """端点接入登记：认证方式、请求头、参数模板与接入状态整包写入。

    同一端点编号重复提交只更新一条待处理记录；同地址冲突按批次/认证模板优先级仲裁，
    落败方隔离并按接口地址回写失败原因。任一字段未落库都不会返回成功。
    """
    entry, receipt = service.save_registration(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=receipt.get("失败原因") or "接入登记被拒绝", entry=receipt)
    return ActionResult(ok=True, message=receipt.get("回执说明") or "接入登记已保存", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条API端点执行提交校验、激活端点、停用端点；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
