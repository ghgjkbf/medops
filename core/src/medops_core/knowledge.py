"""Knowledge base: fault-cause & handling documents (design doc §5.2).

Retrieval is deterministic keyword scoring — no embedding dependency
(DeepSeek ships no embedding API; pgvector/Ollama unavailable on this
host, plan P4c 调研): score = 3 x title hits + 1 x body hits over ASCII
words and CJK 2-grams, ties broken by newest. This function is the
swap-in point for vector retrieval when an embedding provider lands.

`seed_builtin()` idempotently loads one cause/handling document per fault
scenario (devices/engine/scenarios) plus two workflow docs, so the
demo's Agent can answer 故障原因/怎么处理 out of the box.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: F401

from medops_core.models import Alert, KnowledgeDoc

_TITLE_WEIGHT = 3


def _terms(query: str) -> list[str]:
    """ASCII words (underscore-split, lowercased) + CJK runs as 2-grams."""
    terms: list[str] = []
    for word in re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+", query.lower()):
        if re.fullmatch(r"[a-z0-9_]+", word):
            terms.extend(w for w in word.split("_") if len(w) >= 2)
        elif len(word) == 1:
            terms.append(word)
        else:
            terms.extend(word[i : i + 2] for i in range(len(word) - 1))
    return terms


def score_doc(query: str, title: str, content: str) -> int:
    terms = _terms(query)
    if not terms:
        return 0
    t = title.lower()
    c = content.lower()
    return sum(
        (_TITLE_WEIGHT if term in t else 0) + (1 if term in c else 0) for term in terms
    )


# --------------------------------------------------------------------- CRUD
async def add_document(
    factory: async_sessionmaker, title: str, content: str, meta: dict | None = None
) -> int:
    async with factory() as s:
        row = KnowledgeDoc(title=title, content=content, meta=meta or {})
        s.add(row)
        await s.commit()
        return row.id


def _row_dict(row: KnowledgeDoc) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "meta": row.meta,
        "created_at": row.created_at.isoformat(),
    }


async def list_documents(factory: async_sessionmaker) -> list[dict[str, Any]]:
    async with factory() as s:
        rows = (
            await s.scalars(
                select(KnowledgeDoc).order_by(KnowledgeDoc.created_at.desc())
            )
        ).all()
    return [_row_dict(r) for r in rows]


async def search_documents(
    factory: async_sessionmaker, query: str, limit: int = 5
) -> list[dict[str, Any]]:
    async with factory() as s:
        rows = (await s.scalars(select(KnowledgeDoc))).all()
    scored = [(score_doc(query, r.title, r.content), r) for r in rows]
    scored = [(sc, r) for sc, r in scored if sc > 0]
    scored.sort(key=lambda pair: pair[1].created_at, reverse=True)  # newest first
    scored.sort(key=lambda pair: -pair[0])  # stable: score desc, ties keep newest
    return [
        {**_row_dict(r), "score": sc}
        for sc, r in scored[:limit]
    ]


async def delete_document(factory: async_sessionmaker, doc_id: int) -> bool:
    async with factory() as s:
        row = (await s.scalars(select(KnowledgeDoc).where(KnowledgeDoc.id == doc_id))).first()
        if row is None:
            return False
        await s.delete(row)
        await s.commit()
        return True


# ------------------------------------------------------------- builtin seed
def _doc(title: str, device_type: str, scenario: str, cause: str, steps: str) -> dict:
    content = f"【故障原因】{cause}\n【处理方法】{steps}"
    return {
        "title": title,
        "content": content,
        "meta": {"source": "builtin", "device_type": device_type, "scenario": scenario},
    }


SEED_DOCS: list[dict[str, Any]] = [
    _doc(
        "CT 球管过热（tube_overheat）：原因与处理", "ct", "tube_overheat",
        "球管冷却系统退化：水冷流量下降、风冷滤网堵塞或冷却水温升高，导致连续曝光后管温爬升。",
        "1) 检查冷却水流量与水温是否在规格内；2) 清洗或更换风冷滤网；"
        "3) 降低连续曝光负荷，安排间歇冷却；4) 复测仍超限则停机并联系厂家评估球管寿命。",
    ),
    _doc(
        "CT PACS 断连（ct_pacs_disconnect）：原因与处理", "ct", "ct_pacs_disconnect",
        "PACS 网络中断、交换机端口故障或 PACS 接收服务停止，影像无法归档。",
        "1) 检查网线与交换机端口指示灯；2) ping PACS 主机确认链路；"
        "3) 确认 DICOM 端口（104/11112）可达；4) 链路恢复后本地排队影像会自动重传，无需手工干预。",
    ),
    _doc(
        "呼吸机氧电池漂移（o2_cell_drift）：原因与处理", "ventilator", "o2_cell_drift",
        "氧电池电化学老化（寿命约 1-2 年），输出偏移导致氧浓度测量不准。",
        "1) 执行 100% 氧定标确认漂移量；2) 漂移超限更换氧电池；"
        "3) 更换后重新定标并记录更换日期。",
    ),
    _doc(
        "呼吸机管路泄漏（ventilator_leak）：原因与处理", "ventilator", "ventilator_leak",
        "管路连接松动、积水杯密封圈老化或病人端气囊漏气，气道压/潮气量下降。",
        "1) 逐段检查管路连接并紧固；2) 更换老化密封圈；3) 检查气囊充气压力；"
        "4) 报警未消除则整根更换管路。",
    ),
    _doc(
        "DR 磁盘写满（disk_full）：原因与处理", "dr", "disk_full",
        "影像归档长期未清理、检查量大或存储配额耗尽。",
        "1) 清理临时目录与失败重传残留；2) 将旧检查归档至 PACS 后释放本地空间；"
        "3) 配置自动清理策略；4) 必要时扩容。",
    ),
    _doc(
        "DR 发生器过热（dr_generator_overheat）：原因与处理", "dr", "dr_generator_overheat",
        "发生器冷却风扇故障或风道堵塞，kV 输出超差伴随温度爬升。",
        "1) 停机检查风扇电源与转速；2) 清理风道灰尘；3) 更换风扇后复测 kV 输出稳定性。",
    ),
    _doc(
        "DR 探测器制冷衰减（dr_detector_cooling）：原因与处理", "dr", "dr_detector_cooling",
        "探测器制冷电源或 TEC 老化、机房环境温度过高，探测器温度超出工作窗口。",
        "1) 检查制冷电源输出；2) 改善机房空调环境温度；"
        "3) 制冷衰减超限联系厂家更换 TEC 组件。",
    ),
    _doc(
        "心电导联脱落（ecg_lead_off）：原因与处理", "ecg", "ecg_lead_off",
        "电极膏干涸、贴片松脱或导联线弯折断裂，波形 SNR 骤降。",
        "1) 重新处理皮肤并更换电极贴片；2) 检查导联线接头，弯折断裂则更换导联线；"
        "3) 复测波形质量。",
    ),
    _doc(
        "心电电池低电量（ecg_battery_low）：原因与处理", "ecg", "ecg_battery_low",
        "电池充放电循环到达设计寿命，容量衰减导致低电量告警。",
        "1) 接入主电源继续监护；2) 执行电量计校准；3) 更换电池并按环保要求回收旧电池。",
    ),
    _doc(
        "告警分级与响应流程", "general", "workflow",
        "info 级别记录观察即可；warning 需要安排计划性检查；critical 必须立即处理并自动创建工单。",
        "critical 告警：平台自动创建关联工单，工程师接单后按状态机处理"
        "（待接单→处理中→待验证→已关闭），处理结论写入维保记录。",
    ),
    _doc(
        "工单处理流程", "general", "workflow",
        "工单状态机：pending（待接单）→ in_progress（处理中）"
        "→ awaiting_verification（待验证）→ closed（已关闭）。",
        "非法迁移（如 pending 直接 closed）会被拒绝；"
        "每一步处理内容应同步到维保记录，便于追溯与知识积累。",
    ),
]


async def seed_builtin(factory: async_sessionmaker) -> int:
    """Insert builtin docs whose title is missing. Returns count inserted."""
    existing = {d["title"] for d in await list_documents(factory)}
    inserted = 0
    for doc in SEED_DOCS:
        if doc["title"] not in existing:
            await add_document(factory, doc["title"], doc["content"], doc["meta"])
            inserted += 1
    return inserted


async def enrich_alerts(factory: async_sessionmaker, alerts: list) -> None:
    """Attach the top knowledge hit to each alert's attribution (in memory +
    persisted by id). Best-effort: no hits -> attribution untouched."""
    for alert in alerts:
        query = alert.message or ""
        if not query:
            continue
        hits = await search_documents(factory, query, limit=1)
        if not hits:
            continue
        top = hits[0]
        suggestion = f"{top['title']}：{top['content'][:120]}"
        new_attr = (
            f"{alert.attribution}\n知识库建议：{suggestion}"
            if alert.attribution
            else f"知识库建议：{suggestion}"
        )
        alert.attribution = new_attr  # in-memory (notifier / work-order text)
        async with factory() as s:  # persisted by id (alert may be detached)
            await s.execute(
                update(Alert).where(Alert.id == alert.id).values(attribution=new_attr)
            )
            await s.commit()
