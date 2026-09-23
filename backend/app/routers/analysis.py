from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import date
from urllib.parse import quote, urlencode
from ..database import get_db
from ..models import Batch, Pond, StockingRecord, FeedingRecord, CostRecord, HarvestSale, WaterQualityRecord, MedicationRecord
from ..schemas import CultureCycleAnalysis, BatchTraceability, BatchInfo, PondInfo
from ..services import batch_numbers as bn

router = APIRouter(
    prefix="/api/analysis",
    tags=["养殖周期分析"]
)

TRACE_BY_NUMBER_PATH = "/api/analysis/trace-by-number"


def _other_query(request: Request) -> list:
    return [(k, v) for k, v in request.query_params.multi_items() if k != "n"]


def _strict_quote(value, safe="/", encoding="utf-8", errors=None):
    # 与 batches 路由相同："/"→%2F、空格→%20，编码唯一
    return quote(str(value), safe="", encoding=encoding, errors=errors or "strict")


def _query_encode(params: list) -> str:
    return urlencode(params, doseq=True, quote_via=_strict_quote)


def _trace_location(raw_n: str, other: list) -> str:
    params = list(other)
    try:
        value = bn.normalize_batch_number(raw_n)
    except bn.BatchNumberError:
        value = raw_n
    params.append(("n", value))
    return f"{TRACE_BY_NUMBER_PATH}?{_query_encode(params)}"


# 字面静态路由先声明（路由顺序契约）
@router.get("/trace-by-number", response_model=BatchTraceability, summary="按批次号追溯(规范入口)")
def trace_by_batch_number(request: Request, n: str, db: Session = Depends(get_db)):
    """规范入口：GET /api/analysis/trace-by-number?n=批次号

    非规范输入或历史别名 301 到规范地址；不存在 404 / 格式非法 422 / 冲突 409。
    """
    other = _other_query(request)
    try:
        canonical_form = bn.normalize_batch_number(n)
    except bn.BatchNumberError as exc:
        raise HTTPException(status_code=422, detail=f"批次号格式非法: {exc}")

    if canonical_form != n:
        return RedirectResponse(_trace_location(n, other), status_code=301)

    result = bn.resolve(db, n)
    if result.status == bn.FOUND:
        if result.is_alias or result.canonical != n:
            return RedirectResponse(_trace_location(result.canonical, other), status_code=301)
        return _build_traceability(db, result.batch)
    if result.status == bn.CONFLICT:
        raise HTTPException(
            status_code=409,
            detail=(
                f"批次号规范化冲突: '{n}' 不存在，但大小写不同的"
                f"'{result.matched}' 已存在；批次号大小写敏感且不得混用"
            ),
        )
    raise HTTPException(status_code=404, detail=f"批次号 '{canonical_form}' 不存在")


@router.get("/trace-by-number/{legacy:path}", include_in_schema=False)
def legacy_trace_by_batch_number(legacy: str, request: Request):
    """旧链接兼容：/trace-by-number/{batch_number}/ 永久跳转并保留查询参数。"""
    raw_n = legacy.rstrip("/")
    return RedirectResponse(_trace_location(raw_n, _other_query(request)), status_code=301)

@router.get("/cycle/{batch_id:int}/", response_model=CultureCycleAnalysis)
def analyze_cycle(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    
    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    
    initial_quantity = db.query(func.sum(StockingRecord.quantity)).filter(
        StockingRecord.batch_id == batch.id
    ).scalar() or 0
    
    harvest_weight = db.query(func.sum(HarvestSale.weight)).filter(
        HarvestSale.batch_id == batch.id
    ).scalar() or 0
    
    feed_total = db.query(func.sum(FeedingRecord.feed_quantity)).filter(
        FeedingRecord.batch_id == batch.id
    ).scalar() or 0
    
    total_cost = db.query(func.sum(CostRecord.amount)).filter(
        CostRecord.batch_id == batch.id
    ).scalar() or 0
    
    total_revenue = db.query(func.sum(HarvestSale.total_amount)).filter(
        HarvestSale.batch_id == batch.id
    ).scalar() or 0
    
    harvest_date = batch.actual_harvest_date
    days_cultured = None
    if harvest_date:
        days_cultured = (harvest_date - batch.stocking_date).days
    
    survival_rate = 0
    if initial_quantity > 0 and harvest_weight > 0:
        avg_weight_per_fish = 0.5
        estimated_survival = harvest_weight / avg_weight_per_fish
        survival_rate = (estimated_survival / initial_quantity) * 100
    
    feed_conversion_ratio = 0
    if harvest_weight > 0 and feed_total > 0:
        feed_conversion_ratio = feed_total / harvest_weight
    
    yield_per_mu = 0
    if pond and pond.area > 0:
        yield_per_mu = harvest_weight / pond.area
    
    profit = total_revenue - total_cost
    
    costs = db.query(
        CostRecord.cost_type,
        func.sum(CostRecord.amount).label('total')
    ).filter(
        CostRecord.batch_id == batch.id
    ).group_by(CostRecord.cost_type).all()
    
    cost_breakdown = {c.cost_type: c.total for c in costs}
    
    known_types = ['feed', 'medicine', 'labor', 'electricity']
    other_cost = sum(
        amount for cost_type, amount in cost_breakdown.items() 
        if cost_type not in known_types
    )
    
    cost_summary_dict = {
        "feed_cost": cost_breakdown.get('feed', 0),
        "medicine_cost": cost_breakdown.get('medicine', 0),
        "labor_cost": cost_breakdown.get('labor', 0),
        "electricity_cost": cost_breakdown.get('electricity', 0),
        "other_cost": other_cost,
        "total_cost": total_cost
    }
    
    feeding_summary_dict = db.query(
        FeedingRecord.feed_type,
        func.sum(FeedingRecord.feed_quantity).label('total_quantity'),
        func.count(FeedingRecord.id).label('feeding_count')
    ).filter(
        FeedingRecord.batch_id == batch.id
    ).group_by(FeedingRecord.feed_type).all()
    
    total_feed_weight = feed_total
    feeding_count = sum(f.feeding_count for f in feeding_summary_dict)
    avg_daily_feed = 0
    if days_cultured and days_cultured > 0:
        avg_daily_feed = total_feed_weight / days_cultured
    
    feeding_summary_result = {
        "total_feed_weight": total_feed_weight,
        "feeding_count": feeding_count,
        "avg_daily_feed": avg_daily_feed
    }
    
    return CultureCycleAnalysis(
        batch_number=batch.batch_number,
        pond_name=pond.name if pond else "未知",
        species=batch.species,
        stocking_date=batch.stocking_date,
        harvest_date=harvest_date,
        days_cultured=days_cultured,
        initial_quantity=initial_quantity,
        harvest_weight=harvest_weight,
        survival_rate=round(survival_rate, 2),
        feed_total=feed_total,
        feed_conversion_ratio=round(feed_conversion_ratio, 2),
        area=pond.area if pond else 0,
        yield_per_mu=round(yield_per_mu, 2),
        total_cost=total_cost,
        total_revenue=total_revenue,
        profit=profit,
        cost_summary=cost_summary_dict,
        feeding_summary=feeding_summary_result
    )

@router.get("/traceability/{batch_id:int}/", response_model=BatchTraceability)
def batch_traceability(batch_id: int, db: Session = Depends(get_db)):
    batch = db.query(Batch).filter(Batch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="批次不存在")
    return _build_traceability(db, batch)


def _build_traceability(db: Session, batch: Batch) -> BatchTraceability:
    pond = db.query(Pond).filter(Pond.id == batch.pond_id).first()
    
    stocking_records = db.query(StockingRecord).filter(
        StockingRecord.batch_id == batch.id
    ).all()
    
    feeding_records = db.query(FeedingRecord).filter(
        FeedingRecord.batch_id == batch.id
    ).all()
    
    water_quality_records = db.query(WaterQualityRecord).filter(
        WaterQualityRecord.batch_id == batch.id
    ).all()
    
    medication_records = db.query(MedicationRecord).filter(
        MedicationRecord.batch_id == batch.id
    ).all()
    
    cost_records = db.query(CostRecord).filter(
        CostRecord.batch_id == batch.id
    ).all()
    
    harvest_sales = db.query(HarvestSale).filter(
        HarvestSale.batch_id == batch.id
    ).all()
    
    return BatchTraceability(
        batch=BatchInfo(
            batch_number=batch.batch_number,
            species=batch.species,
            stocking_date=batch.stocking_date,
            harvest_date=batch.actual_harvest_date,
            status=batch.status,
            pond_id=batch.pond_id
        ),
        pond_info=PondInfo(
            name=pond.name if pond else None,
            area=pond.area if pond else None,
            water_depth=pond.water_depth if pond else None
        ),
        stocking_records=[
            {
                "species": r.species,
                "quantity": r.quantity,
                "source": r.source,
                "batch_number": r.batch_number,
                "stocking_date": r.created_at.date() if hasattr(r, 'created_at') else None
            } for r in stocking_records
        ],
        feeding_records=[
            {
                "feeding_date": r.feeding_date,
                "feed_type": r.feed_type,
                "quantity": r.feed_quantity,
                "unit": "kg"
            } for r in feeding_records
        ],
        water_quality_records=[
            {
                "record_date": r.record_date,
                "water_temperature": r.water_temperature,
                "ph_value": r.ph_value,
                "dissolved_oxygen": r.dissolved_oxygen
            } for r in water_quality_records
        ],
        medication_records=[
            {
                "medication_date": r.medication_date,
                "medication_name": r.drug_name,
                "dosage": r.dosage,
                "unit": r.dosage_unit
            } for r in medication_records
        ],
        cost_records=[
            {
                "cost_date": r.cost_date,
                "cost_type": r.cost_type,
                "amount": r.amount,
                "description": r.description
            } for r in cost_records
        ],
        harvest_sales=[
            {
                "sale_date": r.sale_date,
                "weight": r.weight,
                "unit_price": r.unit_price,
                "total_amount": r.total_amount,
                "buyer": r.buyer
            } for r in harvest_sales
        ]
    )
