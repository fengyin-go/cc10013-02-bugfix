"""接口管理业务规则：状态流转、字段校验、接入登记的完整写入与冲突仲裁都收在这里。

修复要点：
1. 登记保存采用「整包写入」——认证方式、请求头配置、参数模板与接入状态等字段在一次
   提交里一起落库；任一必填字段缺失就整体判定失败，不允许出现只写入编号和地址的残缺记录。
2. 同一「端点编号」重复提交只更新一条待处理记录（按编号 upsert），不会生成第二条待处理记录。
3. 按「规范化接口地址」做冲突仲裁：同一规范化地址只允许一条可执行（待处理/已接入）记录，
   冲突的另一条被隔离并按接口地址回写失败原因，隔离记录仍保留完整请求头供回看。
4. 认证模板冲突按优先级判定：修正批次高于旧导入批次，其次完整认证模板高于空认证模板，
   最后后提交者优先；一次请求始终只能关联一套完整认证配置。
5. 回执按端点编号关联；被隔离（拒绝）项不会出现在可执行清单里。
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from typing import Any

from app.store import store

MODULE = "endpoint"
# 被隔离的接入登记与接入回执单独建表，不污染接口管理主清单的待处理统计。
QUARANTINE_MODULE = "endpoint_quarantine"
RECEIPT_MODULE = "endpoint_receipt"

REQUIRED_FIELDS = ["端点编号", "接口地址", "请求方法"]
# 一次完整接入登记必须整包落库的字段：认证方式、请求头、参数模板与业务字段一起写入。
FULL_FIELDS = [
    "端点编号",
    "接口地址",
    "请求方法",
    "认证方式",
    "请求头配置",
    "参数模板",
    "响应格式",
    "超时秒数",
]
# 接入状态（与主清单的流程状态分开维护，登记保存时作为一次完整写入的一部分）。
ONBOARD_PENDING = "待接入"
ONBOARD_ACCEPTED = "已接入"
ONBOARD_REJECTED = "已驳回"

STATUS_ORDER = ["草稿", "待校验", "已激活", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "激活端点": "已激活", "停用端点": "已停用"}
NEGATIVE_ACTIONS = ["停用端点"]

# 批次优先级：本次修正批次高于旧导入批次，数值越大优先级越高。
BATCH_PRIORITY = {"修正批次": 2, "导入批次": 1}
DEFAULT_BATCH = "修正批次"
# 可执行清单包含的流程状态（已停用与被驳回的端点不可执行）。
EXECUTABLE_STATUSES = {"草稿", "待校验", "已激活"}


def normalize_address(address: Any) -> str:
    """把接口地址规范化，用于判断两个地址是否指向同一个接入目标。

    规则：去首尾空白与末尾斜杠；带协议时小写 scheme/host、去掉默认端口；
    查询参数按键排序。/v2/orders 这类相对路径直接规范成统一的路径形态。
    """
    raw = str(address or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        scheme_part, rest = raw.split("://", 1)
        # 先把协议与主机段转小写（路径/查询保留原大小写），保证 HTTP://A.COM 能正确归一。
        if "/" in rest:
            host_part, tail = rest.split("/", 1)
            normalized_input = f"{scheme_part.lower()}://{host_part.lower()}/{tail}"
        else:
            normalized_input = f"{scheme_part.lower()}://{rest.lower()}"
        parts = urlsplit(normalized_input)
        scheme = parts.scheme.lower()
        host = parts.hostname or ""
        # 去掉默认端口，避免 http://x:80 与 http://x 被判成两个地址。
        try:
            port = parts.port
        except ValueError:
            port = None
        default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        netloc = host if default_port else parts.netloc
        query = "&".join(sorted(p for p in parts.query.split("&") if p))
        path = parts.path.rstrip("/")
        normalized = urlunsplit((scheme, netloc, path, query, parts.fragment))
    else:
        # 相对路径：去掉末尾斜杠，保留原始大小写（路径通常区分大小写）。
        normalized = raw.rstrip("/")
    return normalized


def has_full_auth(values: dict[str, Any]) -> bool:
    """判断本次请求是否带了一套完整的认证配置（认证方式+请求头+参数模板都非空）。"""
    return all(str(values.get(field) or "").strip() for field in ("认证方式", "请求头配置", "参数模板"))


class EndpointService:
    # ------------------------------------------------------------------ 列表/明细
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        address: str | None = None,
        method: str | None = None,
        onboard_status: str | None = None,
        executable: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(MODULE))
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("端点编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if address:
            # 支持按接口地址检索：原始地址包含匹配，或规范化地址相等/包含匹配。
            needle = address.strip()
            needle_norm = normalize_address(needle)
            rows = [
                row
                for row in rows
                if needle in str(row.get("接口地址", ""))
                or (needle_norm and needle_norm in normalize_address(row.get("接口地址")))
            ]
        if method:
            rows = [row for row in rows if method.strip() in str(row.get("请求方法", ""))]
        if onboard_status:
            rows = [row for row in rows if row.get("接入状态") == onboard_status]
        if executable:
            rows = [
                row
                for row in rows
                if row.get("status") in EXECUTABLE_STATUSES
                and row.get("接入状态") != ONBOARD_REJECTED
                and not row.get("失败原因")
            ]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def onboard_stats(self) -> dict[str, int]:
        """接口管理待处理区的接入统计（全量，不受分页影响）。"""
        rows = store.rows(MODULE)
        return {
            "total": len(rows),
            "accepted": sum(1 for row in rows if row.get("接入状态") == ONBOARD_ACCEPTED),
            "pending": sum(1 for row in rows if row.get("接入状态") == ONBOARD_PENDING),
            "rejected": sum(1 for row in rows if row.get("接入状态") == ONBOARD_REJECTED),
            "quarantined": len(store.rows(QUARANTINE_MODULE)),
        }

    def list_quarantine(
        self, *, address: str | None = None, page: int = 1, size: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        """隔离区：被拒绝的接入登记，完整请求头仍可回看，支持按接口地址过滤。"""
        rows = list(store.rows(QUARANTINE_MODULE))
        if address:
            needle = address.strip()
            rows = [row for row in rows if needle in str(row.get("接口地址", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def list_receipts(
        self, *, code: str | None = None, page: int = 1, size: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        """接入回执：按端点编号关联，登记成功或被拒都在这里留痕。"""
        rows = list(store.rows(RECEIPT_MODULE))
        if code:
            rows = [row for row in rows if code.strip() in str(row.get("端点编号", ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    # ------------------------------------------------------------ 通用登记（保留）
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """通用登记入口：缺字段时说明原因而不是静默丢弃。

        内部统一走完整接入登记逻辑，保证认证方式、请求头、参数模板等字段随主字段整包落库。
        """
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        entry, receipt = self.save_registration(values)
        if entry is None:
            # 被隔离时把失败原因透传给调用方，不显示成功。
            return None, [receipt.get("失败原因", "接入登记被拒绝")]
        return entry, []

    # ------------------------------------------------------------ 接入登记（核心修复）
    def save_registration(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """保存一次端点接入登记，返回 (主清单记录或 None, 回执)。

        整包写入 + 同编号 upsert + 同地址冲突仲裁。任一字段未落库都不会返回成功。
        """
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            receipt = self._write_receipt(
                values,
                ONBOARD_REJECTED,
                f"缺少必填字段：{'、'.join(missing)}，本次提交整体未落库",
            )
            return None, receipt

        batch = str(values.get("导入批次") or DEFAULT_BATCH).strip() or DEFAULT_BATCH
        code = str(values.get("端点编号")).strip()
        normalized = normalize_address(values.get("接口地址"))

        # 先在局部把完整记录组装好，校验通过后再一次性写回，保证原子性。
        candidate = {field: values.get(field) for field in FULL_FIELDS}
        candidate["规范化地址"] = normalized
        candidate["导入批次"] = batch
        candidate["认证模板"] = str(values.get("认证模板") or values.get("认证方式") or "").strip()
        candidate["接入状态"] = ONBOARD_PENDING
        candidate["失败原因"] = ""

        rows = store.rows(MODULE)
        existing = next((row for row in rows if str(row.get("端点编号", "")).strip() == code), None)

        # 同编号重新提交：清理该编号上一次的隔离记录与失败回执，避免历史拒绝状态残留。
        self._clear_history(code)

        # 同地址冲突仲裁：找同一规范化地址下、其它编号的可执行记录。
        conflict = self._find_conflict(rows, normalized, exclude_code=code)
        if conflict is not None:
            winner, loser, reason = self._arbitrate(candidate, conflict)
            # 仲裁失败的一方隔离，并按接口地址回写失败原因。
            self._quarantine(loser, reason)
            self._write_receipt(loser, ONBOARD_REJECTED, reason)
            if winner is conflict:
                # 本次提交被既有记录压制：本次登记不落主清单，返回失败回执。
                # 若该编号此前已有主清单记录（重新提交落败），同步移除，避免残缺/过期记录残留。
                if existing is not None:
                    rows.remove(existing)
                receipt = self._write_receipt(candidate, ONBOARD_REJECTED, reason)
                return None, receipt
            # 本次提交胜出：旧的冲突记录已经被隔离，需要从主清单移除。
            rows.remove(conflict)

        if existing is not None:
            # 同一端点编号再次提交：只更新这一条待处理记录，保留原 id，不新增第二条。
            entry_id = existing.get("id")
            existing.clear()
            existing.update(candidate)
            existing["id"] = entry_id
            existing["status"] = "待校验"
            existing["pending"] = True
            existing["abnormal"] = False
            entry = existing
        else:
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update(candidate)
            entry["status"] = "待校验"
            entry["pending"] = True
            entry["abnormal"] = False
            rows.append(entry)

        receipt = self._write_receipt(entry, ONBOARD_ACCEPTED, "接入登记已整包落库，进入待处理区")
        return entry, receipt

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"API端点 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于接口管理可执行范围"
        # 被驳回/隔离的端点不能激活，残缺配置不允许进入可执行清单。
        if entry.get("接入状态") == ONBOARD_REJECTED or entry.get("失败原因"):
            return None, "该端点接入登记已被驳回，请修正配置后重新提交"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        if action == "激活端点":
            entry["接入状态"] = ONBOARD_ACCEPTED
        return entry, f"API端点已{action}"

    # ------------------------------------------------------------------ 内部规则
    def _find_conflict(
        self, rows: list[dict[str, Any]], normalized: str, *, exclude_code: str
    ) -> dict[str, Any] | None:
        if not normalized:
            return None
        for row in rows:
            if str(row.get("端点编号", "")).strip() == exclude_code:
                continue
            if normalize_address(row.get("规范化地址") or row.get("接口地址")) != normalized:
                continue
            # 只与仍在可执行范围、未被驳回的记录做冲突仲裁。
            if row.get("接入状态") == ONBOARD_REJECTED or row.get("失败原因"):
                continue
            # 不同端点编号落在同一规范化地址即构成冲突（同编号重复提交已在上方按 upsert 处理），
            # 完整认证模板优先、空认证被隔离的裁决统一交给 _arbitrate。
            return row
        return None

    def _arbitrate(
        self, candidate: dict[str, Any], conflict: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """冲突优先级判定，返回 (胜出记录, 落败记录, 失败原因)。"""
        reason = (
            f"规范化接口地址 {candidate['规范化地址']} 已被其它端点占用，"
            "一次请求只能关联一套完整认证配置"
        )
        cand_priority = BATCH_PRIORITY.get(candidate.get("导入批次", ""), 0)
        conf_priority = BATCH_PRIORITY.get(conflict.get("导入批次", ""), 0)
        if cand_priority != conf_priority:
            if cand_priority > conf_priority:
                return candidate, conflict, reason + "；修正批次优先级高于旧导入批次，旧记录已隔离"
            return conflict, candidate, reason + "；旧导入批次优先级更高，本次登记被拒绝"
        # 同批次：完整认证模板优先于空认证模板。
        cand_full = has_full_auth(candidate)
        conf_full = has_full_auth(conflict)
        if cand_full != conf_full:
            if cand_full:
                return candidate, conflict, reason + "；完整认证模板优先于空认证模板，旧记录已隔离"
            return conflict, candidate, reason + "；对方认证模板完整，本次空认证登记被拒绝"
        # 再相同则后提交者优先（候选即本次提交）。
        return candidate, conflict, reason + "；优先级相同，后提交记录覆盖旧记录"

    def _quarantine(self, record: dict[str, Any], reason: str) -> None:
        """把落败记录完整快照写入隔离区，请求头配置原样保留供回看。"""
        bucket = store.rows(QUARANTINE_MODULE)
        snapshot = {
            "id": max((int(row.get("id", 0)) for row in bucket), default=0) + 1,
            "失败原因": reason,
        }
        for field in FULL_FIELDS + ["规范化地址", "导入批次", "认证模板", "接入状态"]:
            snapshot[field] = record.get(field)
        bucket.append(snapshot)

    def _write_receipt(self, record: dict[str, Any], onboard_status: str, reason: str) -> dict[str, Any]:
        """按端点编号写接入回执；同一编号重复提交只保留最新一条回执。"""
        bucket = store.rows(RECEIPT_MODULE)
        code = str(record.get("端点编号", "")).strip()
        receipt = next((row for row in bucket if str(row.get("端点编号", "")).strip() == code), None)
        payload = {
            "端点编号": code,
            "接口地址": record.get("接口地址", ""),
            "规范化地址": normalize_address(record.get("接口地址")),
            "认证模板": record.get("认证模板", ""),
            "接入状态": onboard_status,
            "失败原因": reason if onboard_status == ONBOARD_REJECTED else "",
            "回执说明": reason,
        }
        if receipt is None:
            payload["id"] = max((int(row.get("id", 0)) for row in bucket), default=0) + 1
            bucket.append(payload)
            return payload
        receipt_id = receipt.get("id")
        receipt.clear()
        receipt.update(payload)
        receipt["id"] = receipt_id
        return receipt

    def _clear_history(self, code: str) -> None:
        """重新提交同一编号时，清掉该编号此前的隔离记录与失败回执。"""
        quarantine = store.rows(QUARANTINE_MODULE)
        quarantine[:] = [
            row for row in quarantine if str(row.get("端点编号", "")).strip() != code
        ]
        receipts = store.rows(RECEIPT_MODULE)
        receipts[:] = [row for row in receipts if str(row.get("端点编号", "")).strip() != code]
