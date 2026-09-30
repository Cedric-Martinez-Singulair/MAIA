import json

from pydantic import TypeAdapter
from langchain_core.tools import tool
from maia_utils import *

@tool
def count_turbines(country_id: int | None = None, model_id: int | None = None, 
avg_type: Literal["country", "turbine_model", "turbine_age"] | None = None
) -> str:
    '''Count the turbines, optionally averaged.

    Use this for aggregate questions about the whole turbine population, a country or a turbine model. 
    It does NOT return individual turbine names — for that, use count_damage_on_a_site_turbines instead.

    Args:
        country_id: Restrict to one country.
        model_id: Restrict to one turbine model. Can be combined country_id.
        avg_type: Grouping key for the average. Combinable with any of the filters above.

    Returns:
        If avg_type is None: The count of turbine. 
        Otherwise: a dict mapping each group (country, turbine model, or turbine age)
        to the number of turbine in that group.'''
    print("TOOL_CALL count_turbines", country_id, model_id, avg_type)
    
    if avg_type is None:
        req_str = "SELECT COUNT(*) "
    elif avg_type == 'country':
        req_str = "SELECT countries.label_en, COUNT(*) "
    elif avg_type == 'turbine_model':
        req_str = "SELECT models.name, COUNT(*) "
    elif avg_type == 'turbine_age':
        req_str = "SELECT EXTRACT(YEAR FROM AGE(NOW(), entry_service))::INT AS turbine_age, COUNT(*) "
    
    req_str += " \
        FROM turbines \
        JOIN sites ON sites.id = turbines.site_id \
        JOIN countries ON countries.id = sites.country_id \
        JOIN models ON models.id = turbines.model_name \
        WHERE turbines.deleted_at IS NULL AND sites.deleted_at IS NULL \
    "
    
    req_optional_params = []
    if country_id is not None:
        req_str += "AND sites.country_id = %s "; req_optional_params.append(country_id)
    if model_id is not None:
        req_str += "AND turbines.model_name = %s "; req_optional_params.append(model_id)
    
    if avg_type == 'country':
        req_str += "GROUP BY countries.label_en "
    elif avg_type == 'turbine_model':
        req_str += "GROUP BY models.name "
    elif avg_type == 'turbine_age':
        req_str += "GROUP BY turbine_age "
    
    print("--- REQ", req_str, req_optional_params)
    if len(req_optional_params) == 0: TW_DB_CURSOR.execute(req_str)
    elif len(req_optional_params) == 1: TW_DB_CURSOR.execute(req_str, (req_optional_params[0], ))
    elif len(req_optional_params) == 2: TW_DB_CURSOR.execute(req_str, (req_optional_params[0], req_optional_params[1], ))
    
    if avg_type is None:
        res_turbine_count = None
    else:
        res_turbine_count = {}
    
    for row in TW_DB_CURSOR:
        if avg_type is None:
            res_turbine_count = row[0]
        else:
            avg_key, turbine_count = row
            if avg_key is not None: 
                res_turbine_count[str(avg_key)] = turbine_count
    
    if isinstance(res_turbine_count, dict):
        resp = TypeAdapter(dict[str, int]).dump_json(res_turbine_count).decode()
    else:
        resp = TypeAdapter(int).dump_json(res_turbine_count).decode()
    print("--- RES", resp)
    return resp

@tool
def get_turbines_location(turbine_ids: list[int]) -> str:
    '''Get the location of given turbines in latitude and longitude
    always give all the informations when you return the datas.
    
    Args:
        turbine_ids: a list of given turbines
    Returns:
        A list of object Turbine with their latitude and longitude
    '''
    print("TOOL_CALL get_turbines_location", turbine_ids)
    
    req_str = "SELECT turbines.id,turbines.name,turbines.longitude,turbines.latitude FROM turbines WHERE turbines.id = ANY (%s)"
    req_params = turbine_ids
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, (req_params,))
    
    result = []
    
    for row in TW_DB_CURSOR:
        result.append(Turbine(
            id=row[0],
            name=row[1],
            longitude=row[2],
            latitude=row[3])
        )
    
    resp = TypeAdapter(list[Turbine]).dump_json(result).decode(); print("--- RES", resp)
    return resp

@tool
def get_sites_location(site_ids: list[int]) -> str:
    '''Get the location of given sites in latitude and longitude
    
    Args:
        site_ids: a list of given site_id 
    Returns:
        A list of object Site with their latitude and longitude
    '''
    print("TOOL_CALL get_sites_location ", site_ids)
    
    req_str = "SELECT sites.id,sites.name,sites.longitude,sites.latitude FROM sites WHERE sites.id =ANY (%s)"
    req_params = site_ids
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, (req_params, ))
    
    result = []
    
    for row in TW_DB_CURSOR:
        result.append(Site(
            id=row[0],
            name=row[1],
            longitude=row[2],
            latitude=row[3])
        )
    
    resp = TypeAdapter(list[Site]).dump_json(result).decode(); print("--- RES", resp)
    return resp

 
# Hauteurs de travail standard des cherry pickers du marché (m)
CHERRY_PICKER_HEIGHTS: list[int] = [27, 37, 40, 47, 51, 54, 65, 72, 75, 90, 100]
SUSPENDED_PLATFORM = "Suspended platform"
SAFETY_MARGIN_M = 2.0  # marge entre la hauteur du dégât et la hauteur de travail
MAX_DAMAGES_DETAIL = 150 
 
def reachable(height: float | None, working_height: float) -> bool:
    return height is not None and height + SAFETY_MARGIN_M <= working_height
 
 
def recommend_equipment(height: float | None) -> str:
    if height is None:
        return "Unknown (missing tower_height or radius)"
    for h in CHERRY_PICKER_HEIGHTS:
        if reachable(height, h):
            return f"Cherry picker {h} m"
    return SUSPENDED_PLATFORM


@tool
def turbine_damage_height(inspection_id: int,
                          turbine_ids: list[int] | None = None,
                          defect_type_ids: list[int] | None = None):
    """
    Get the repair height of blade damages and how many of them each cherry picker height can reach.
    ALWAYS call this tool whenever repair is mentioned in any way (repair, fix, maintenance,
    intervention, "what should I do about this damage"...), even if the user does not ask
    for heights or equipment. Use the result to advise the user on how to carry out the repair.
 
    Args:
        inspection_id: The inspection (planification) ID. Alone, it covers the whole site.
        turbine_ids: Turbines to include. None = all turbines of the inspection.
        defect_type_ids: Damage types to include. A damage name can have several IDs,
            so include all IDs with the same or similar name. None = all damages.
 
    Returns:
        - cherry_picker_coverage: for each standard cherry picker height, the number and share
          of damages it can reach, and the number of turbines fully repairable with it
        - damages_needing_suspended_platform: damages too high for any cherry picker
        - turbines: heights (min / avg / max), max severity, smallest equipment and damage detail
        repair height = tower_height - radius (blade pointing down).
        - If detail_note is present, tell the user the detail was reduced and offer to
        narrow down to one or a few turbines (turbine_ids) for the full damage detail.
 
    How to answer:
        - Focus on coverage: "with a X m cherry picker you can reach N damages (P%)",
          give orders of magnitude for a few relevant heights, and say what is left
          for a suspended platform.
        - Heights are standard market sizes only: never mention a provider or brand, and
          never say the equipment is available, owned or rented by us.
        - Never give any price estimate.
        - Severity <= 2 is NEVER critical: never call these damages critical, urgent or
          dangerous. Present them as minor damages to handle during planned maintenance.
        - Only damages with severity > 2 can be critical and should be prioritized.
        - Remind that a cherry picker needs a flat, accessible ground near the tower.
    """
    conditions = ["incident_records.planification_id = %s", "incident_records.deleted_at IS NULL"]
    req_params: list = [inspection_id]
    if turbine_ids:
        conditions.append("turbines.id = ANY(%s)")
        req_params.append(turbine_ids)
    if defect_type_ids:
        conditions.append("incident_records.defect_type_id = ANY(%s)")
        req_params.append(defect_type_ids)
 
    req_str = f"""
        SELECT
            turbines.id AS turbine_id,
            turbines.tower_height,
            turbines.tower_height - incident_records.radius AS damage_repair_height,
            incident_records.id AS incident_id,
            incident_records.criticality_id AS severity,
            parts.label_en AS part,
            components.label_en AS component,
            defect_types.label_en AS defect_type
        FROM turbines
        JOIN incident_records ON incident_records.turbine_id = turbines.id
        LEFT JOIN defect_types ON defect_types.id = incident_records.defect_type_id
        LEFT JOIN components ON components.id = incident_records.component_id
        LEFT JOIN parts ON parts.id = incident_records.part_id
        WHERE {" AND ".join(conditions)}
        AND incident_records.component_id <> 1
        ORDER BY turbines.id, damage_repair_height DESC
    """
    TW_DB_CURSOR.execute(req_str, req_params)
    cols = [d[0] for d in TW_DB_CURSOR.description]
    rows = [r if isinstance(r, dict) else dict(zip(cols, r)) for r in TW_DB_CURSOR.fetchall()]
 
    by_turbine: dict[int, dict] = {}
    for row in rows:
        t = by_turbine.setdefault(row["turbine_id"], {
            "tower_height": float(row["tower_height"]) if row["tower_height"] is not None else None,
            "damages": [],
        })
        t["damages"].append(DamageRepairHeight(
            incident_id=row["incident_id"],
            part=row["part"],
            component=row["component"],
            defect_type=row["defect_type"],
            severity=row["severity"],
            damage_repair_height=float(row["damage_repair_height"]) if row["damage_repair_height"] is not None else None,
        ))
 
    turbines: list[TurbineRepairSummary] = []
    for turbine_id, t in by_turbine.items():
        heights = [d.damage_repair_height for d in t["damages"] if d.damage_repair_height is not None]
        severities = [d.severity for d in t["damages"] if d.severity is not None]
        max_h = max(heights) if heights else None
        turbines.append(TurbineRepairSummary(
            turbine_id=turbine_id,
            tower_height=t["tower_height"],
            nb_damages=len(t["damages"]),
            max_severity=max(severities) if severities else None,
            avg_damage_repair_height=round(sum(heights) / len(heights), 2) if heights else None,
            min_damage_repair_height=min(heights) if heights else None,
            max_damage_repair_height=max_h,
            recommended_equipment=recommend_equipment(max_h),
            damages=t["damages"],
        ))
 
    all_damages = [d for t in turbines for d in t.damages]
    total = len(all_damages)
    coverage = []
    for h in CHERRY_PICKER_HEIGHTS:
        ok = [d for d in all_damages if reachable(d.damage_repair_height, h)]
        coverage.append(CherryPickerCoverage(
            working_height=h,
            reachable_damages=len(ok),
            reachable_damages_pct=round(len(ok) * 100 / total, 1) if total else 0.0,
            reachable_critical_damages=sum(1 for d in ok if d.severity is not None and d.severity > 2),
            fully_repairable_turbines=sum(
                1 for t in turbines if t.damages and all(reachable(d.damage_repair_height, h) for d in t.damages)
            ),
        ))
    detail_note = None
    if total > MAX_DAMAGES_DETAIL:
        for t in turbines:
            t.damages = [d for d in t.damages if d.severity is not None and d.severity > 2]
        nb_detailed = sum(len(t.damages) for t in turbines)
        if nb_detailed > MAX_DAMAGES_DETAIL:
            for t in turbines:
                t.damages = []
            detail_note = (f"{total} damages found: too many to detail. Statistics cover all damages, "
                           "but damage detail is omitted. Ask the user to narrow down to one or a few turbines.")
        else:
            detail_note = (f"{total} damages found: only the {nb_detailed} damages with severity > 2 are detailed. "
                           "Statistics cover all damages.")
 
    report = RepairHeightReport(
        nb_damages=total,
        nb_critical_damages=sum(1 for d in all_damages if d.severity is not None and d.severity > 2),
        damages_needing_suspended_platform=sum(
            1 for d in all_damages
            if d.damage_repair_height is not None and not reachable(d.damage_repair_height, CHERRY_PICKER_HEIGHTS[-1])
        ),
        cherry_picker_coverage=coverage,
        turbines=turbines,
        detail_note=detail_note,
    )
 
    resp = TypeAdapter(RepairHeightReport).dump_json(report).decode()
    print("--- RES", resp)
    return resp
