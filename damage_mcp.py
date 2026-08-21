from langchain_core.tools import tool
from maia_utils import *

from typing import Literal

@tool
def count_damage_on_turbines(
damage_type_id: int, 
country_id: int | None = None, model_id: int | None = None, 
avg_type: Literal["country", "turbine_model", "turbine_age"] | None = None
) -> str:
    '''Count damages of a given type across a group of turbines, optionally averaged.

    Use this for aggregate questions about the whole turbine population, a country or a turbine model. 
    It does NOT return individual turbine names — for that, use count_damage_on_a_site_turbines instead.

    Args:
        damage_type_id: Id of the damage type to count.
        country_id: Restrict to one country.
        model_id: Restrict to one turbine model. Can be combined country_id.
        avg_type: Grouping key for the average. Combinable with any of the filters above.

    Returns:
        If avg_type is None: a list of damage counts, one per turbine.
        Otherwise: a dict mapping each group (country, turbine model, or turbine age)
        to the average damage count for the turbines in that group.'''
    print("TOOL_CALL count_damage_on_turbines", damage_type_id, country_id, model_id, avg_type)
    
    if avg_type is None:
        req_str = "SELECT incident_records.turbine_id, incident_records.planification_id, COUNT(*) "
    elif avg_type == 'country':
        req_str = "SELECT incident_records.turbine_id, incident_records.planification_id, countries.label_en, COUNT(*) "
    elif avg_type == 'turbine_model':
        req_str = "SELECT incident_records.turbine_id, incident_records.planification_id, models.name, COUNT(*) "
    elif avg_type == 'turbine_age':
        req_str = "SELECT incident_records.turbine_id, incident_records.planification_id, EXTRACT(YEAR FROM AGE(planifications.date, entry_service))::INT AS turbine_age, COUNT(*) "
    
    req_str += " \
        FROM incident_records \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        JOIN planifications ON planifications.id = incident_records.planification_id \
        JOIN sites ON sites.id = turbines.site_id \
        JOIN countries ON countries.id = sites.country_id \
        JOIN models ON models.id = turbines.model_name \
        WHERE incident_records.deleted_at IS NULL AND turbines.deleted_at IS NULL AND sites.deleted_at IS NULL \
    "
    
    req_str += "AND incident_records.defect_type_id = %s "
    
    req_optional_params = []
    if country_id is not None:
        req_str += "AND sites.country_id = %s "; req_optional_params.append(country_id)
    if model_id is not None:
        req_str += "AND turbines.model_name = %s "; req_optional_params.append(model_id)
    
    if avg_type is None:
        req_str += "GROUP BY incident_records.turbine_id, incident_records.planification_id "
    elif avg_type == 'country':
        req_str += "GROUP BY incident_records.turbine_id, incident_records.planification_id, countries.label_en "
    elif avg_type == 'turbine_model':
        req_str += "GROUP BY incident_records.turbine_id, incident_records.planification_id, models.name "
    elif avg_type == 'turbine_age':
        req_str += "GROUP BY incident_records.turbine_id, incident_records.planification_id, turbine_age "
    
    if avg_type is None:
        req_str += "LIMIT 100 "
    
    print("--- REQ", req_str, damage_type_id, req_optional_params)
    if len(req_optional_params) == 0: TW_DB_CURSOR.execute(req_str, (damage_type_id, ))
    elif len(req_optional_params) == 1: TW_DB_CURSOR.execute(req_str, (damage_type_id, req_optional_params[0], ))
    elif len(req_optional_params) == 2: TW_DB_CURSOR.execute(req_str, (damage_type_id, req_optional_params[0], req_optional_params[1], ))
    
    if avg_type is None:
        damage_counts = []
    else:
        raw_damage_counts = {}
        damage_counts = {}
    
    for row in TW_DB_CURSOR:
        if avg_type is None:
            turbine_id, planification_id, damage_count = row
            damage_counts.append(damage_count)
        else:
            turbine_id, planification_id, avg_key, damage_count = row
            if avg_key is not None: 
                if avg_key not in raw_damage_counts: 
                    raw_damage_counts[avg_key] = []
                raw_damage_counts[avg_key].append(damage_count)
    
    if avg_type is not None:
        for avg_key in raw_damage_counts:
             damage_counts[avg_key] = mean(raw_damage_counts[avg_key])
    
    print("--- RES", damage_counts)
    return str(damage_counts)

@tool
def count_damage_on_a_site_turbines(
damage_type_id: int, site_id: int, 
planification_id: int | None = None) -> str:
    '''Count damages of a given type on each individual turbine of one site.

    Use this when the answer must name specific turbines, or to follow how damages
    evolved over time on a site. For aggregate figures across several sites,
    countries or turbine models, use count_damage_on_turbines instead.
    
    A planification is an inspection campaign carried out on the site at a given date.
    
    Args:
        damage_type_id: Id of the damage type to count.
        site_id: The site whose turbines are counted.
        planification_id: Restrict the result to a single planification. If omitted, every planification recorded for this site is returned.

    Returns:
        A list of PlanificationDamageCountResult, one item per planification. 
        Each item holds the planification date (YYYY-mm-dd) and,
        for every turbine of the site, its name, model, age at that date, and the number of damages of the requested type.'''
    print("TOOL_CALL count_damage_on_a_site_turbines", damage_type_id, site_id, planification_id)
    
    req_str = " \
        SELECT planifications.id, CAST(planifications.date AS VARCHAR), turbines.id, turbines.name, COUNT(*) \
        FROM incident_records \
        JOIN planifications ON planifications.id = incident_records.planification_id \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        WHERE incident_records.deleted_at IS NULL AND turbines.deleted_at IS NULL AND planifications.deleted_at IS NULL \
            AND incident_records.defect_type_id = %s AND turbines.site_id = %s \
    "
    req_optional_params = []
    if planification_id is not None:
        req_str += "AND incident_records.planification_id = %s "; req_optional_params.append(planification_id)
    
    req_str += " \
        GROUP BY planifications.id, planifications.date, turbines.id, turbines.name \
        ORDER BY planifications.id \
    "
    
    print("--- REQ", req_str, damage_type_id, site_id, req_optional_params)
    if len(req_optional_params) == 0: TW_DB_CURSOR.execute(req_str, (damage_type_id, site_id, ))
    elif len(req_optional_params) == 1: TW_DB_CURSOR.execute(req_str, (damage_type_id, site_id, req_optional_params[0], ))
    
    damage_counts = []
    found_planification_id = []
    for row in TW_DB_CURSOR:
        planification_id, planification_date, turbine_id, turbine_name, damage_count = row
        
        if planification_id not in found_planification_id:
            found_planification_id.append(planification_id)
            damage_counts.append(PlanificationDamageCountResult(
                planification=Planification(id=planification_id, date=planification_date),
                turbine_damage_counts=[]
            ))
        
        damage_counts[-1].turbine_damage_counts.append(TurbineDamageCountResult(
            turbine=Turbine(id=turbine_id, name=turbine_name),
            damage_count_filter="Only count damage_type_id = " + str(damage_type_id),
            damage_count=damage_count
        ))
    
    print("--- RES", damage_counts)
    return str(damage_counts)

@tool
def estimate_repair_cost(
planification_id: int,
turbine_id: int | None = None,
damage_type_id: int | None = None,
) -> str:
    '''Estimate the cost of repairing the damages reported at one planification.
    
    Covers a single planification of a single site.
    
    Args:
        planification_id: Required. The inspection whose damages are priced.
        turbine_id: Restrict the estimate to one turbine of that site.
        damage_type_id: Restrict the estimate to one damage type.
        
    Returns:
        A list of TurbineRepairCost, one per turbine. Each turbine holds its
        damages grouped by (severity, damage type), with the number of damages in
        the group and the estimated repair cost for that group, in USD. No overall
        total is provided — sum the groups if needed.'''
    print("TOOL_CALL estimate_repair_cost", planification_id, turbine_id, damage_type_id)
    
    general_cost_by_sev = {}
    TW_DB_CURSOR.execute("SELECT * FROM sesame_damage_general_cost")
    for row in TW_DB_CURSOR:
        id, control_id, severity_id, repair_time, unit_cost, mark_up = row
        general_cost_by_sev[severity_id] = unit_cost
        
    damage_cost_coefs = {}
    damage_cost_coef_max_count = 1
    TW_DB_CURSOR.execute("SELECT * FROM sesame_damages_coefficients")
    for row in TW_DB_CURSOR:
        id, count, coeff = row
        damage_cost_coefs[count] = coeff
        
        if count > damage_cost_coef_max_count: 
            damage_cost_coef_max_count = count
        
    req_str = " \
        SELECT turbines.id, turbines.name, incident_records.defect_type_id, incident_records.criticality_id, COUNT(*) \
        FROM incident_records \
        JOIN planifications ON planifications.id = incident_records.planification_id \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        WHERE incident_records.planification_id = %s \
    "
    
    req_optional_params = []
    if turbine_id is not None:
        req_str += "AND incident_records.turbine_id = %s "; req_optional_params.append(turbine_id)
    if damage_type_id is not None:
        req_str += "AND incident_records.defect_type_id = %s "; req_optional_params.append(damage_type_id)
        
    req_str += " \
        GROUP BY turbines.id, turbines.name, incident_records.defect_type_id, incident_records.criticality_id \
        ORDER BY turbines.id \
    "
    
    print("--- REQ", req_str, planification_id, req_optional_params)
    if len(req_optional_params) == 0: TW_DB_CURSOR.execute(req_str, (planification_id, ))
    elif len(req_optional_params) == 1: TW_DB_CURSOR.execute(req_str, (planification_id, req_optional_params[0], ))
    elif len(req_optional_params) == 2: TW_DB_CURSOR.execute(req_str, (planification_id, req_optional_params[0], req_optional_params[1], ))
    
    turbine_repair_costs = []
    found_turbine_id = []
    for row in TW_DB_CURSOR:
        turbine_id, turbine_name, row_damage_type_id, severity_id, damage_count = row
        true_severity = severity_id
        if true_severity == 6: true_severity = 0
        
        if severity_id in general_cost_by_sev:
            coef_damage_count = damage_count
            if coef_damage_count > damage_cost_coef_max_count:
                coef_damage_count = damage_cost_coef_max_count
            repair_cost = general_cost_by_sev[severity_id] * damage_cost_coefs[coef_damage_count]
            
            explanation = "The base cost for this damage severity is " + str(general_cost_by_sev[severity_id])
            explanation += " and since there is " + str(damage_count) + "damage it's multiplied by " + str(damage_cost_coefs[coef_damage_count])
            
            if turbine_id not in found_turbine_id:
                found_turbine_id.append(turbine_id)
                turbine_repair_costs.append(TurbineRepairCost(
                    turbine=Turbine(id=turbine_id, name=turbine_name),
                    damage_group_repair_cost=[]
                ))
            
            turbine_repair_costs[-1].damage_group_repair_cost.append(DamageGroupRepairCost(
                severity=true_severity,
                damage_type_id=row_damage_type_id,
                damage_count=damage_count,
                repair_cost=repair_cost,
                explanation=explanation
            ))
    
    print("--- RES", turbine_repair_costs)
    return str(turbine_repair_costs)

@tool
def get_individual_damage_infos(individual_damage_id) -> str:
    '''Give information about a specific damage
    
    Use it if you must give information about a damage.
    If you want repair frequency for this type of damage, use estimate_damage_repair_frequency instead.
    
    Args:
        individual_damage_id: Id of the individual damage for which you need informations.
        
    Returns:
        A Damage object with all the informations about this damage.'''
    print("TOOL_CALL get_individual_damage_infos", individual_damage_id)
        
    req_str = " \
        SELECT incident_records.id, components.id, components.label_en, defect_types.id, defect_types.label_en, criticality_id,  \
        sites.id, sites.name, turbines.id, turbines.name, components_turbines.name, parts.label_en, incident_records.radius,  \
        planifications.id, planifications.date::TEXT \
        FROM incident_records \
        JOIN components ON components.id = incident_records.component_id \
        JOIN defect_types ON defect_types.id = incident_records.defect_type_id \
        JOIN parts ON parts.id = incident_records.part_id  \
        JOIN components_turbines ON components_turbines.id = incident_records.component_turbine_id  \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        JOIN sites ON sites.id = turbines.site_id \
        JOIN planifications ON planifications.id = incident_records.planification_id \
        WHERE incident_records.id = %s \
    "
    print("--- REQ", req_str, individual_damage_id)
    
    TW_DB_CURSOR.execute(req_str, (individual_damage_id, ))
    for row in TW_DB_CURSOR:
        dmg_id, part_dmgd_id, part_dmgd, dmg_type_id, dmg_type, sev_id, site_id, site, turbine_id, turbine, blade, face, radius, planif_id, planif_date = row
    
    damage_infos = Damage(
        id=individual_damage_id,
        damaged_part=BladePart(id=part_dmgd_id, name=part_dmgd),
        damage_type=DamageType(id=dmg_type_id, name=dmg_type),
        site=Site(id=site_id, name=site),
        turbine=Turbine(id=turbine_id, name=turbine),
        blade=blade,
        face=face,
        radius=radius,
        planification=Planification(id=planif_id, date=planif_date),
    )
    
    print("--- RES", damage_infos)
    return damage_infos

@tool
def estimate_damage_repair_frequency(individual_damage_id) -> str:
    '''How often damages comparable to a given one end up being repaired.
    
    Use this to tell whether a specific damage is usually repaired at the next inspection, one year later, or usually left as is.
    
    Comparable damages are those, located on the same turbine model, same face and same radius.
    Also, the comparable damages are sharing the same damage type, the same part damaged and the same severity.
    
    Args:
        individual_damage_id: Id of the individual damage used as the reference.
        
    Returns:
        A dict with two keys:
        - total_data: number of comparable damages found in the database.
        - total_repaired: how many of them were repaired at the next inspection.
        Divide the second by the first to get the repair rate. 
        Below ~30 samples the rate is not reliable and should be reported with its sample size.'''
    print("TOOL_CALL estimate_damage_repair_frequency", individual_damage_id)
    
    ### GET the specific damage (individual_damage_id) infos ###
    
    req_str = " \
        SELECT incident_records.component_id, incident_records.defect_type_id, incident_records.criticality_id, \
        incident_records.part_id, incident_records.radius, turbines.model_name \
        FROM incident_records \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        WHERE incident_records.id = %s \
    "
    print("--- REQ", req_str, individual_damage_id)
    
    TW_DB_CURSOR.execute(req_str, (individual_damage_id, ))
    for row in TW_DB_CURSOR:
        part_damaged_id, damage_type_id, severity_id, face_id, radius, model_id = row
    
    ### GET similar damages ###
    
    req_str = " \
        SELECT incident_records.id, turbine_id, planification_id \
        FROM incident_records \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        WHERE incident_records.id  <> %s \
        AND incident_records.component_id = %s AND incident_records.defect_type_id = %s AND incident_records.criticality_id = %s \
        AND incident_records.part_id = %s AND incident_records.radius = %s AND turbines.model_name = %s \
    "
    print("--- REQ", req_str, individual_damage_id, part_damaged_id, damage_type_id, severity_id, face_id, radius, model_id)
    
    similar_damages = []
    TW_DB_CURSOR.execute(req_str, (individual_damage_id, part_damaged_id, damage_type_id, severity_id, face_id, radius, model_id, ))
    for row in TW_DB_CURSOR:
        similar_id, similar_turbine_id, similar_planification_id = row
        similar_damages.append({'id': similar_id, 'turbine_id': similar_turbine_id, 'planification_id': similar_planification_id})
    
    ### GET matching damages ###
    
    total_data = 0; total_repaired = 0
    for similar_damage in similar_damages:
        req_str = "SELECT available_asc \
            FROM matching_availability \
            WHERE turbine_id = %s AND planification_id = %s"
        # print("--- REQ", req_str, similar_damage['turbine_id'], similar_damage['planification_id'])
        TW_DB_CURSOR.execute(req_str, (similar_damage['turbine_id'], similar_damage['planification_id'], ))
        
        available_asc = None
        for row in TW_DB_CURSOR:
            available_asc = row[0]
        
        if available_asc is not None:
            req_str = "SELECT matched_damage_id FROM matching_asc WHERE damage_id = %s"
            # print("--- REQ", req_str, similar_damage['id'])
            TW_DB_CURSOR.execute(req_str, (similar_damage['id'], ))
            
            for row in TW_DB_CURSOR:
                total_data += 1
                if row[0] is None: total_repaired += 1
    
    print("--- RES", total_data, total_repaired)
    return str({'total_data': total_data, 'total_repaired': total_repaired})