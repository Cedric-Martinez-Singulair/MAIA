import json
from statistics import mean

from langchain_core.tools import tool
from maia_utils import *
from aep_utils import get_aep_loss_turbine
from wind.wind_calculator import WINDCalculator
from stats_utils import *

from typing import Literal
from dataclasses import asdict

from statistics import mean, median


@tool
def get_damage_path(individual_damage_id: int) -> str:
    '''Get the path of the inspection image on which a given damage is visible.

    Use this when the client asks to see, show or display the picture of a damage.
    Call get_individual_damage_infos instead if the client wants the characteristics
    of the damage (type, severity, location) rather than its image.

    Args:
        individual_damage_id: damage_id of the individual damage, as returned by the
            damage listing tools.

    Returns:
        The path of the image, relative to the inspection images root.'''
    print("TOOL_CALL get_damage_path", individual_damage_id)
    
    req_str = " \
        SELECT incident_records.report_path \
        FROM incident_records \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        JOIN sites ON sites.id = turbines.site_id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = sites.id \
        WHERE incident_records.id = %s \
    "
    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID'], individual_damage_id]
    
    TW_DB_CURSOR.execute(req_str, req_params)
    for row in TW_DB_CURSOR:
        dmg_img_path = row[0]
    
    print("--- RES", dmg_img_path)
    return dmg_img_path

@tool
def count_damage_on_turbines(
damage_type_id: int, 
country_id: int | None = None, model_id: int | None = None, 
avg_type: Literal["country", "turbine_model", "turbine_age"] | None = None
) -> str:
    '''Count damages of a given type across a group of turbines, optionally averaged.

    Use this for aggregate questions about damages on the whole turbine population, a country or a turbine model. 
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
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = sites.id \
        JOIN countries ON countries.id = sites.country_id \
        JOIN models ON models.id = turbines.model_name \
        WHERE incident_records.dismissed = FALSE AND incident_records.decision_id != 0 AND incident_records.deleted_at IS NULL AND turbines.deleted_at IS NULL AND sites.deleted_at IS NULL \
        AND incident_records.defect_type_id = %s \
    "
    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID'], damage_type_id]
    
    if country_id is not None:
        req_str += "AND sites.country_id = %s "; req_params.append(country_id)
    if model_id is not None:
        req_str += "AND turbines.model_name = %s "; req_params.append(model_id)
    
    if avg_type is None:
        req_str += "GROUP BY incident_records.turbine_id, incident_records.planification_id "
    elif avg_type == 'country':
        req_str += "GROUP BY incident_records.turbine_id, incident_records.planification_id, countries.label_en "
    elif avg_type == 'turbine_model':
        req_str += "GROUP BY incident_records.turbine_id, incident_records.planification_id, models.name "
    elif avg_type == 'turbine_age':
        req_str += "GROUP BY incident_records.turbine_id, incident_records.planification_id, turbine_age "
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
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
             damage_counts[avg_key] = round(mean(raw_damage_counts[avg_key]), 2)
    
    print("--- RES", damage_counts)
    return str(damage_counts)

DamageAggregation = Literal["damage_type", "blade_component", "severity"]
@tool
def count_damage_on_a_site_turbines(
site_id: int, 
location_precision: Literal["turbine", "blade", "face", "radius"],
damage_aggregations: list[DamageAggregation],
inspection_id: int | None = None,
damage_type_id: int | None = None, severities: list[int] | None = None,
turbine_ids: list[int] | None = None,
) -> str:
    '''Show or summarize damages on a site, per turbine (or blade/face if requested).

    Use this when the user asks to see, show, list, or compare damages on a site;
    Use this when the answer must name specific turbines, or to follow how damages evolved over time on a site. 
    For aggregate figures across several sites, countries or turbine models, use count_damage_on_turbines instead.
    
    An inspection is an inspection campaign carried out on the site at a given date.
    If location_precision is radius you must filter on a damage type with damage_type_id.
    
    Args:
        site_id: The site whose turbines are counted.
        
        location_precision: Specify how finely damages are located in the result: 
            one row per turbine, per blade, per blade face, or by radius.
        damage_aggregations: Specify which characteristics split damages into separate
            counts. Pass an empty list for a single total per location. "blade_component"
            groups by the component of the blade affected (coat, laminate, lightning receptor...),
            which is independent of location_precision.
        inspection_id: Restrict the result to a single inspection. If omitted, every inspection recorded for this site is returned.
        damage_type_id: Restrict to one damage type.
        severities: Restrict to some severities. Can be combined with damage_type_id.
        turbine_ids: Restricts the result to some turbines. When left empty, every damaged turbine of the inspection is returned.

    Returns:
        One InspectionDamageCountResult per campaign, each holding the inspection
        date (YYYY-mm-dd) and one row per location at the requested precision. Every row
        carries the turbine name, the blade and face when
        applicable, the value of each requested aggregation, and the damage count.
        Turbines with no damage don't appear.'''
    print("TOOL_CALL count_damage_on_a_site_turbines", site_id, location_precision, damage_aggregations, inspection_id, damage_type_id, severities, turbine_ids)
    
    req_str = "SELECT planifications.id AS planif_id, CAST(planifications.date AS VARCHAR) AS planif_date, turbines.id AS turbine_id, turbines.name AS turbine_name, "
    
    if location_precision == 'blade': req_str += "components_turbines.name AS blade, "
    elif location_precision == 'face': req_str += "components_turbines.name AS blade, parts.label_en AS face, "
    elif location_precision == 'radius': req_str += "components_turbines.name AS blade, parts.label_en AS face, incident_records.radius, "
    if 'damage_type' in damage_aggregations: req_str += "defect_types.id AS damage_type_id, defect_types.label_en AS damage_type, "
    if 'blade_component' in damage_aggregations: req_str += "components.id AS blade_component_id, components.label_en AS blade_component, "
    if 'severity' in damage_aggregations: req_str += "incident_records.criticality_id AS severity, "
    
    req_str += "COUNT(*) AS damage_count "
    
    req_str += " \
        FROM incident_records \
        JOIN planifications ON planifications.id = incident_records.planification_id \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = turbines.site_id \
    "
    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID']]
    
    if location_precision in ['blade', 'face', 'radius']: req_str += "JOIN components_turbines ON components_turbines.id = incident_records.component_turbine_id "
    if location_precision in ['face', 'radius']: req_str += "JOIN parts ON parts.id = incident_records.part_id "
    if 'damage_type' in damage_aggregations: req_str += "JOIN defect_types ON defect_types.id = incident_records.defect_type_id "
    if 'blade_component' in damage_aggregations: req_str += "JOIN components ON components.id = incident_records.component_id "
    # No need to JOIN for severity
    
    req_str += " \
        WHERE incident_records.dismissed = FALSE AND incident_records.decision_id != 0 AND incident_records.deleted_at IS NULL AND turbines.deleted_at IS NULL AND planifications.deleted_at IS NULL \
            AND turbines.site_id = %s \
    "
    req_params.append(site_id)
    
    if inspection_id is not None:
        req_str += "AND incident_records.planification_id = %s "; req_params.append(inspection_id)
    if damage_type_id is not None:
        req_str += "AND incident_records.defect_type_id = %s "; req_params.append(damage_type_id)
        
    if severities is not None and len(severities) > 0:
        req_str += "AND incident_records.criticality_id IN ("
        for severity in severities:
            severity_id = severity if severity != 0 else 6
            req_str += "%s, "; req_params.append(severity_id)
        req_str = req_str[:-2] + ") " # Remove last comma
    
    if turbine_ids is not None and len(turbine_ids) > 0 :
        req_str += "AND turbines.id IN ("
        for turbine_id in turbine_ids:
            req_str += "%s, "; req_params.append(turbine_id)
        req_str = req_str[:-2] + ") "
    
    req_str += "GROUP BY planifications.id, planifications.date, turbines.id, turbines.name, "
    
    if location_precision == 'blade': req_str += "components_turbines.name, "
    elif location_precision == 'face': req_str += "components_turbines.name, parts.label_en, "
    elif location_precision == 'radius': req_str += "components_turbines.name, parts.label_en, incident_records.radius, "
    if 'damage_type' in damage_aggregations: req_str += "defect_types.id, defect_types.label_en, "
    if 'blade_component' in damage_aggregations: req_str += "components.id, components.label_en, "
    if 'severity' in damage_aggregations: req_str += "incident_records.criticality_id, "
    
    req_str = req_str[:-2] # Remove the last comma
    req_str += " ORDER BY planifications.id, turbines.id "
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    damage_counts = []
    found_inspection_id = []
    found_insp_turbine_id = []
    
    if damage_type_id is None and severities is None: damage_count_filter = "Count all damages"
    elif damage_type_id is not None and severities is None: damage_count_filter = "Only count damage_type_id = " + str(damage_type_id)
    elif damage_type_id is None and severities is not None: damage_count_filter = "Only count severities in " + str(severities)
    elif damage_type_id is not None and severities is not None: damage_count_filter = "Only count damage_type_id = " + str(damage_type_id) + " and severity = " + str(severity)
    
    dmg_count_group_cpt = 0
    
    req_columns = [col[0] for col in TW_DB_CURSOR.description]
    for row in TW_DB_CURSOR:
        record = dict(zip(req_columns, row))
        if 'severity' in record and record['severity'] == 6: record['severity'] = 0
        
        if record['planif_id'] not in found_inspection_id:
            found_inspection_id.append(record['planif_id'])
            damage_counts.append(InspectionDamageCountResult(
                inspection=Inspection(
                    id=record['planif_id'], 
                    date=record['planif_date'],
                    published_date=str(get_publish_date(record['planif_id']))),
                turbine_damage_counts=[]
            ))
            found_insp_turbine_id = []
        
        if record['turbine_id'] not in found_insp_turbine_id:
            found_insp_turbine_id.append(record['turbine_id'])
            damage_counts[-1].turbine_damage_counts.append(TurbineDamageCountResult(
                turbine=Turbine(id=record['turbine_id'], name=record['turbine_name']),
                damage_count_filter=damage_count_filter,
                damage_counts=[]
            ))
            
        blade_component = BladeComponent(id=record['blade_component_id'], name=record['blade_component']) if 'blade_component_id' in record else None
        damage_type = DamageType(id=record['damage_type_id'], name=record['damage_type']) if 'damage_type_id' in record else None
        damage_counts[-1].turbine_damage_counts[-1].damage_counts.append(DamageCountResult(
            blade=record.get('blade'),
            face=record.get('face'),
            radius=record.get('radius'),
            blade_component=blade_component,
            damage_type=damage_type,
            severity=record.get('severity'),
            damage_count=record['damage_count']
        ))
        dmg_count_group_cpt += 1
        
    if dmg_count_group_cpt > 100:
        resp = "There is too much data to analyse.\n"
        if damage_type_id == None or severities == None:
            resp += "If you can, use the damage filter parameters (damage_type_id, severities) to reduce the data.\n"
        if location_precision != 'turbines':
            resp += "You can propose to the user to use a less precise location than." + location_precision + "\n"
        if turbine_ids == None:
            resp += "You can propose to the user to look at a specific turbine.\n"
        print("--- RES", resp); return resp
    
    print("--- RES", damage_counts)
    return str(damage_counts)

# TODO prendre en compte si la turbine est Approved ou non
@tool
def estimate_repair_cost(
inspection_id: int,
turbine_id: int | None = None,
damage_type_id: int | None = None,
) -> str:
    '''Estimate the cost of repairing the damages reported at one inspection.
    
    Covers a single inspection of a single site.
    
    Args:
        inspection_id: The inspection whose damages are priced.
        turbine_id: Restrict the estimate to one turbine of that site.
        damage_type_id: Restrict the estimate to one damage type.
        
    Returns:
        A list of TurbineRepairCost, one per turbine with damage to repair. Each turbine holds its
        damages grouped by (severity, damage type), with the number of damages in
        the group and the estimated repair cost for that group, in USD. No overall
        total is provided — sum the groups if needed.'''
    print("TOOL_CALL estimate_repair_cost", inspection_id, turbine_id, damage_type_id)
    
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
        SELECT turbines.id, turbines.name, defect_types.id, defect_types.label_en, incident_records.criticality_id, COUNT(*) \
        FROM incident_records \
        JOIN planifications ON planifications.id = incident_records.planification_id \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = turbines.site_id \
        JOIN defect_types ON defect_types.id = incident_records.defect_type_id \
        WHERE incident_records.planification_id = %s \
    "
    
    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID'], inspection_id]
    
    if turbine_id is not None:
        req_str += "AND incident_records.turbine_id = %s "; req_params.append(turbine_id)
    if damage_type_id is not None:
        req_str += "AND incident_records.defect_type_id = %s "; req_params.append(damage_type_id)
        
    req_str += " \
        GROUP BY turbines.id, turbines.name, defect_types.id, defect_types.label_en, incident_records.criticality_id \
        ORDER BY turbines.id \
    "
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    turbine_repair_costs = []
    found_turbine_id = []
    for row in TW_DB_CURSOR:
        turbine_id, turbine_name, row_damage_type_id, damage_type, severity_id, damage_count = row
        true_severity = severity_id
        if true_severity == 6: true_severity = 0
        
        if severity_id in general_cost_by_sev:
            coef_damage_count = damage_count
            if coef_damage_count > damage_cost_coef_max_count:
                coef_damage_count = damage_cost_coef_max_count
            repair_cost = general_cost_by_sev[severity_id] * damage_cost_coefs[coef_damage_count]
            
            explanation = "The base cost for this damage severity is " + str(general_cost_by_sev[severity_id])
            explanation += " and since there is " + str(damage_count) + " damage it's multiplied by " + str(damage_cost_coefs[coef_damage_count])
            
            if turbine_id not in found_turbine_id:
                found_turbine_id.append(turbine_id)
                turbine_repair_costs.append(TurbineRepairCost(
                    turbine=Turbine(id=turbine_id, name=turbine_name),
                    damage_group_repair_cost=[]
                ))
            
            turbine_repair_costs[-1].damage_group_repair_cost.append(DamageGroupRepairCost(
                severity=true_severity,
                damage_type=DamageType(id=row_damage_type_id, name=damage_type),
                damage_count=damage_count,
                repair_cost=repair_cost,
                explanation=explanation
            ))
    
    print("--- RES", turbine_repair_costs)
    return str(turbine_repair_costs)

@tool
def get_individual_damage_infos(individual_damage_id: int) -> str:
    '''Give detailed information about one specific individual damage.
    
    Use it if you want information about a single damage occurrence observed on one blade of one turbine.
    If you want repair frequency for this family of damage, use estimate_damage_repair_frequency instead.
    
    Args:
        individual_damage_id: damage_id of the individual damage, as returned by the damage listing tools.
        
    Returns:
        A Damage object: damage type, severity (1 to 5), affected turbine and site,
        blade and position, detection date and inspection it comes from.'''
    print("TOOL_CALL get_individual_damage_infos", individual_damage_id)
        
    req_str = " \
        SELECT incident_records.id, components.id, components.label_en, defect_types.id, defect_types.label_en, criticality_id,  \
        sites.id, sites.name, turbines.id, turbines.name, components_turbines.name, parts.label_en, incident_records.radius,  \
        planifications.id, planifications.date::TEXT, \
        incident_records.wind, incident_records.wind_severity, incident_records.wind_desc, incident_records.wind_new_desc, incident_records.wind_new_sev \
        FROM incident_records \
        JOIN components ON components.id = incident_records.component_id \
        JOIN defect_types ON defect_types.id = incident_records.defect_type_id \
        JOIN parts ON parts.id = incident_records.part_id  \
        JOIN components_turbines ON components_turbines.id = incident_records.component_turbine_id  \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        JOIN sites ON sites.id = turbines.site_id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = sites.id \
        JOIN planifications ON planifications.id = incident_records.planification_id \
        WHERE incident_records.id = %s \
    "
    
    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID'], individual_damage_id]
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    for row in TW_DB_CURSOR:
        dmg_id, part_dmgd_id, part_dmgd, dmg_type_id, dmg_type, sev_id, site_id, site, turbine_id, turbine, blade, face, radius, planif_id, planif_date, wind, wind_severity, wind_desc, wind_new_desc, wind_new_sev = row

    wind_calculator = WINDCalculator()
    
    damage_infos = Damage(
        id=individual_damage_id,
        damaged_part=BladeComponent(id=part_dmgd_id, name=part_dmgd),
        damage_type=DamageType(id=dmg_type_id, name=dmg_type),
        site=Site(id=site_id, name=site),
        turbine=Turbine(id=turbine_id, name=turbine),
        blade=blade,
        face=face,
        radius=radius,
        inspection=Inspection(
            id=planif_id, date=planif_date, published_date=str(get_publish_date(planif_id))
        ),
        wind=wind_calculator.getDmgWIND({"wind": wind, "wind_severity": wind_severity, "wind_desc": wind_desc, "wind_new_desc": wind_new_desc, "wind_new_sev": wind_new_sev})
    )
    
    print("--- RES", damage_infos)
    return str(damage_infos)

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
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = turbines.site_id \
        WHERE incident_records.id = %s \
    "
    
    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID'], individual_damage_id]
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    for row in TW_DB_CURSOR:
        part_damaged_id, damage_type_id, severity_id, face_id, radius, model_id = row
    
    ### GET similar damages ###
    
    req_str = " \
        SELECT incident_records.id, turbine_id, planification_id \
        FROM incident_records \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = turbines.site_id \
        WHERE incident_records.id  <> %s \
        AND incident_records.component_id = %s AND incident_records.defect_type_id = %s AND incident_records.criticality_id = %s \
        AND incident_records.part_id = %s AND incident_records.radius = %s AND turbines.model_name = %s \
    "
    
    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID'], individual_damage_id, part_damaged_id, damage_type_id, severity_id, face_id, radius, model_id]
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    similar_damages = []
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


# Pale visée -> pales à exclure du calcul (paramètre not_blades)
BLADE_LAYOUT = {
    "A": ["C", "B"],
    "B": ["A", "C"],
    "C": ["A", "B"],
}

@tool
def aep_loss_on_a_site_for_each_turbine(site_id: int,
inspection_id: int | None = None,
aep_loss_blade_needed: bool = False,
criticality: int | None = None) -> str:
    '''Get the AEP (Annual Energy Production) loss of a site and of each of its turbines.

    Use this when the user asks how much energy a site is losing, which turbines
    are losing the most, or wants to compare turbines within a site. For a question
    about a single named turbine, call aep_loss_on_a_turbine instead: it computes
    only that turbine and is much cheaper.

    Set aep_loss_blade_needed=True only when the question is about blades: which
    blade drives a turbine's loss, or how much would be recovered by repairing it.
    That mode runs three extra computations per turbine and is noticeably slower,
    so leave it False for any site- or turbine-level question.

    Losses are computed with the Jensen wake model.

    Args:
        site_id: Identifier of the site. Use get_sites to resolve a site name into its id.
        inspection_id: Optional. Restricts the figures to one inspection.
            When omitted, the most recent inspection available for that site
            is used, which is what you want unless the user explicitly asks about
            an older inspection.
        aep_loss_blade_needed: Optional, default False. Adds a per-blade breakdown 
            (blades A, B and C) to every turbine. Slow — only set it True when the
            user asks about blades specifically.
        criticality: Optional. Damage criticality level, taken from the user's own
            request — pass it only when the user explicitly names a criticality
            level, and pass exactly the level they asked for. Never guess a value
            and never pass a default: when the user says nothing about criticality,
            leave this out and report the current losses.
            It simulates repairs: the result then also carries, for each blade, the
            AEP loss that would remain once all damages of that criticality are
            repaired. Requires aep_loss_blade_needed=True; ignored otherwise. Typical
            question: "how much energy would we recover on this site by repairing
            the level 4 damages?"

    Returns:
        A SiteAEPLoss object with the site (id and name), the inspection the
        figures come from (id and date), the total AEP loss of the site, and
        aep_loss_turbines: one TurbineAEPLoss per turbine, each carrying the
        turbine id and name and its own AEP loss. When aep_loss_blade_needed is
        True, each turbine also carries blade_aep_loss: one BladeAEPLoss per blade
        with its individual loss, plus the post-repair loss when criticality was
        given.
    '''
    print("TOOL_CALL aep_loss_on_a_site_for_each_turbine",
          site_id, inspection_id, aep_loss_blade_needed, criticality)

    # ------------------------------------------------------------------ #
    # 1. Résolution de l'inspection
    #
    # On la résout AVANT la requête principale : les appels imbriqués à
    # get_aep_loss_turbine doivent porter sur la même inspection que les
    # chiffres du site, sinon le détail par pale ne correspond pas au total.
    # ------------------------------------------------------------------ #
    if inspection_id is None:
        TW_DB_CURSOR.execute(
            "SELECT MAX(planification_id) FROM aep_loss WHERE site_id = %s",
            (site_id,),
        )
        row = TW_DB_CURSOR.fetchone()
        resolved_planification_id = row[0] if row else None

        if resolved_planification_id is None:
            return "ERROR: no AEP loss data for this site"
    else:
        resolved_planification_id = inspection_id

    # ------------------------------------------------------------------ #
    # 2. Chiffres par turbine
    #
    # DISTINCT : les jointures peuvent produire plusieurs lignes pour une même
    # turbine, et chaque doublon multipliait le nombre de calculs par pale.
    # fetchall() est indispensable : get_aep_loss_turbine va exécuter d'autres
    # requêtes, et itérer sur TW_DB_CURSOR pendant ce temps écraserait le jeu
    # de résultats en cours.
    # ------------------------------------------------------------------ #
    req_str = (
        "SELECT DISTINCT al.turbine_id, al.aep_loss_jensen, t.name, "
        "s.name, p.date "
        "FROM aep_loss al "
        "JOIN turbines t ON t.id = al.turbine_id "
        "JOIN sites s ON s.id = al.site_id "
        "JOIN planifications p ON p.id = al.planification_id "
        "WHERE al.site_id = %s AND al.planification_id = %s"
    )
    TW_DB_CURSOR.execute(req_str, (site_id, resolved_planification_id))
    rows = TW_DB_CURSOR.fetchall()

    if not rows:
        return "ERROR: no AEP loss data for this site"

    # ------------------------------------------------------------------ #
    # 3. Calculs par pale
    #
    # Une turbine = 3 calculs (6 avec criticality), quel que soit le nombre de
    # lignes qu'elle occupe dans le résultat SQL.
    # ------------------------------------------------------------------ #
    blade_results: dict[tuple[int, str, bool], float] = {}

    if aep_loss_blade_needed:
        turbine_ids = sorted({row[0] for row in rows})

        tasks = []
        for turbine_id in turbine_ids:
            for blade, not_blades in BLADE_LAYOUT.items():
                blade_results[(turbine_id, blade, False)] = get_aep_loss_turbine(
                    site_id=site_id,
                    planification_id=resolved_planification_id,
                    turbine_id=turbine_id,
                    not_blades=not_blades,
                    criticality_id=None,
                )["AEP_loss_Jensen"]
 
                if criticality is not None:
                    blade_results[(turbine_id, blade, True)] = get_aep_loss_turbine(
                        site_id=site_id,
                        planification_id=resolved_planification_id,
                        turbine_id=turbine_id,
                        not_blades=not_blades,
                        criticality_id=criticality,
                    )["AEP_loss_Jensen"]
 
    # ------------------------------------------------------------------ #
    # 4. Construction du résultat
    # ------------------------------------------------------------------ #
    aep_loss_turbines = []
    aep_loss_site = 0.0
    site_name = ""
    inspection_date = ""

    for turbine_id, aep_loss_jensen, turbine_name, site_name, inspection_date in rows:
        blade_aep_loss = None
        if aep_loss_blade_needed:
            blade_aep_loss = [
                BladeAEPLoss(
                    blade=blade,
                    aep_loss_blade=blade_results.get((turbine_id, blade, False)),
                    aep_loss_blade_repair=blade_results.get((turbine_id, blade, True)),
                )
                for blade in BLADE_LAYOUT  # dict ordonné : A, B, C
            ]

        aep_loss_turbines.append(
            TurbineAEPLoss(
                turbine=Turbine(id=turbine_id, name=turbine_name),
                blade_aep_loss=blade_aep_loss,
                aep_loss=aep_loss_jensen,
            )
        )
        aep_loss_site += float(aep_loss_jensen)

    result = SiteAEPLoss(
        site=Site(id=site_id, name=str(site_name)),
        inspection=Inspection(
            id=resolved_planification_id, published_date=str(get_publish_date(resolved_planification_id))
        ),
        aep_loss_site=aep_loss_site,
        aep_loss_turbines=aep_loss_turbines,
    )

    return result.model_dump_json()

@tool
def aep_loss_on_a_turbine(turbine_id: int,
inspection_id: int | None = None,
aep_loss_blade_needed: bool = False,
criticality: int | None = None) -> str:
    '''Get the AEP (Annual Energy Production) loss of one single turbine.

    Use this whenever the question is about one turbine — how much energy it is
    losing, which of its blades loses the most, how much a repair would recover.
    Prefer it over aep_loss_on_a_site_for_each_turbine every time a single
    turbine is named: that tool computes the whole site and is far slower.
    Only fall back to the site tool when the user wants to compare turbines with
    each other or wants the site total.

    Losses are computed with the Jensen wake model.

    Args:
        turbine_id: Identifier of the turbine. Use list_turbines or the site tool
            to resolve a turbine name like "T01" into its id, since turbine names
            repeat across sites.
        inspection_id: Optional. Restricts the figures to one inspection.
            When omitted, the most recent inspection available for that turbine
            is used, which is what you want unless the user explicitly asks about
            an older inspection.
        aep_loss_blade_needed: Optional, default False. Adds the per-blade
            breakdown (blades A, B and C). It runs three extra computations, so
            leave it False when the turbine total is enough.
        criticality: Optional. Damage criticality level, taken from the user's own
            request — pass it only when the user explicitly names a criticality
            level, and pass exactly the level they asked for. Never guess a value
            and never pass a default: when the user says nothing about criticality,
            leave this out and report the current losses.
            It simulates repairs: the result then also carries, for each blade, the
            AEP loss that would remain once all damages of that criticality are
            repaired. Requires aep_loss_blade_needed=True; ignored otherwise.

    Returns:
        The site and the inspection the figures come from, and a TurbineAEPLoss
        with the turbine id, its name and its AEP loss. When aep_loss_blade_needed
        is True, it also carries blade_aep_loss: one BladeAEPLoss per blade with
        its individual loss, plus the post-repair loss when criticality was given.
    '''
    print("TOOL_CALL aep_loss_on_a_turbine",
          turbine_id, inspection_id, aep_loss_blade_needed, criticality)

    # ------------------------------------------------------------------ #
    # 1. Résolution de la inspection
    #
    # Résolue avant tout : les appels à get_aep_loss_turbine doivent porter sur
    # la même inspection que le chiffre affiché, sinon le détail par pale ne
    # correspond pas au total de la turbine.
    # ------------------------------------------------------------------ #
    if inspection_id is None:
        TW_DB_CURSOR.execute(
            "SELECT MAX(planification_id) FROM aep_loss WHERE turbine_id = %s",
            (turbine_id,),
        )
        row = TW_DB_CURSOR.fetchone()
        resolved_planification_id = row[0] if row else None

        if resolved_planification_id is None:
            return "ERROR: no AEP loss data for this turbine"
    else:
        resolved_planification_id = inspection_id

    # ------------------------------------------------------------------ #
    # 2. Chiffre de la turbine
    #
    # site_id vient de la table : l'appelant n'a pas à le connaître, et
    # get_aep_loss_turbine en a besoin pour le calcul de sillage.
    # ------------------------------------------------------------------ #
    req_str = (
        "SELECT DISTINCT al.turbine_id, al.aep_loss_jensen, t.name, "
        "al.site_id, s.name, p.date "
        "FROM aep_loss al "
        "JOIN turbines t ON t.id = al.turbine_id "
        "JOIN sites s ON s.id = al.site_id "
        "JOIN planifications p ON p.id = al.planification_id "
        "WHERE al.turbine_id = %s AND al.planification_id = %s"
    )
    TW_DB_CURSOR.execute(req_str, (turbine_id, resolved_planification_id))
    row = TW_DB_CURSOR.fetchone()

    if row is None:
        return "ERROR: no AEP loss data for this turbine at this inspection"

    (turbine_id, aep_loss_jensen, turbine_name, site_id, site_name, planification_date) = row

    # ------------------------------------------------------------------ #
    # 3. Détail par pale
    #
    # Trois calculs, six avec criticality. Séquentiel : à ce volume les threads
    # coûteraient plus en contention qu'ils ne rapportent.
    # ------------------------------------------------------------------ #
    blade_aep_loss = None

    if aep_loss_blade_needed:
        blade_aep_loss = []
        for blade, not_blades in BLADE_LAYOUT.items():
            current = get_aep_loss_turbine(
                site_id=site_id,
                inspection_id=resolved_planification_id,
                turbine_id=turbine_id,
                not_blades=not_blades,
                criticality_id=None,
            )["AEP_loss_Jensen"]

            repaired = None
            if criticality is not None:
                repaired = get_aep_loss_turbine(
                    site_id=site_id,
                    inspection_id=resolved_planification_id,
                    turbine_id=turbine_id,
                    not_blades=not_blades,
                    criticality_id=criticality,
                )["AEP_loss_Jensen"]

            blade_aep_loss.append(BladeAEPLoss(
                blade=blade,
                aep_loss_blade=current,
                aep_loss_blade_repair=repaired,
            ))

    # ------------------------------------------------------------------ #
    # 4. Résultat
    # ------------------------------------------------------------------ #
    turbine_result = TurbineAEPLoss(
        turbine=Turbine(id=turbine_id, name=turbine_name),
        blade_aep_loss=blade_aep_loss,
        aep_loss=round(aep_loss_jensen,2),
    )

    return json.dumps({
        "site": Site(id=site_id, name=str(site_name)).model_dump(),
        "inspection": Inspection(
            id=resolved_planification_id, published_date=str(get_publish_date(resolved_planification_id))
        ).model_dump(),
        "turbine": turbine_result.model_dump(),
    }, default=str)

@tool
def analyse_crack_evolution(turbine_id: int,
inspection_id: int,
previous_inspection_id: int | None = None,
blade: str | None = None,
only_changed: bool = False) -> str:
    '''Tell how the cracks of a turbine evolved between two inspection campaigns.
 
    Use this when the user asks whether a crack grew, whether damages got worse
    on a turbine, what changed since the last inspection, or which cracks were
    repaired.
 
    Args:
        turbine_id: Identifier of the turbine to analyse.
        inspection_id: The campaign to look at.
        previous_inspection_id: Optional. The campaign to compare against.
            When omitted, the campaign right before inspection_id is used.
        blade: Optional. Restricts the answer to one blade, "A", "B" or "C".
        only_changed: Set True to drop unchanged cracks
            and keep only what is new, grown or repaired.
 
    Returns:
        The two campaigns compared, and one entry per crack with its blade, face,
        radius, shape, severity and WIND index, its current and previous size in
        meters, and a status: new (absent before), grown, stable, or repaired
        (present before, gone now).

        Sizes are lengths in meters, except for multibranched and stripes cracks
        which are areas in square meters and are not compared. Each entry also
        states whether its size comes from a manual measurement or from the image:
        a delta between two different sources is not reliable. growth_was_predicted
        marks a crack whose WIND index had already forecast the growth.
    '''
    print("TOOL_CALL analyse_crack_evolution", turbine_id, inspection_id,
          previous_inspection_id, blade, only_changed)
 
    if previous_inspection_id is None:
        previous_inspection_id = resolve_previous_planification(turbine_id, inspection_id)
        if previous_inspection_id is None:
            res = "ERROR: no earlier inspection campaign with damages for this turbine, there is nothing to compare with"
            print(res)
            return res
 
    full_data = fetch_crack_data(inspection_id, turbine_id)
    previous_full_data = fetch_crack_data(previous_inspection_id, turbine_id)
 
    evolutions = []
 
    for current_blade in ([blade] if blade else BLADES):
        blade_data = select_blade(full_data, current_blade)
        previous_blade_data = select_blade(previous_full_data, current_blade)
 
        if blade_data.empty and previous_blade_data.empty:
            continue
 
        #TODO vérifier si les cracks qui ont "rétrécis" sont remontés ou pas, c'est pas clair
        evolutions.extend(compare_blade_cracks(
            build_cracks_by_face(blade_data),
            build_cracks_by_face(previous_blade_data),
            current_blade,
        ))
 
    if only_changed:
        evolutions = [e for e in evolutions if e.status != "stable"]
 
    #TODO mettre toutes les infos
    res = json.dumps({
        "turbine_id": turbine_id,
        "inspection_id": inspection_id,
        "previous_inspection_id": previous_inspection_id,
        "cracks": [asdict(e) for e in evolutions],
    }, default=str)
 
    print("-- RES", res)
    return res

@tool
def analyse_erosion_evolution(
inspection_id: int,
turbine_id: int | None = None,
site_id: int | None = None,
previous_inspection_id: int | None = None,
blade: str | None = None,
include_areas: bool = True) -> str:
    '''Tell how the leading edge erosion evolved between two inspection campaigns, for one turbine or a whole site.

    Use this when the user asks how the erosion evolved or changed. Erosion is measured as the
    total eroded length along the leading edge of each blade, in meters.

    Pass either turbine_id for a single turbine, or site_id to cover every
    turbine of a site in one call — never both. Prefer site_id over looping
    turbine by turbine: one call reads the whole site at once and is far faster.
    
    When you use the site_id you will receive less data to get a smaller payload 

    Asking about a whole site is not asking about repairs. Report how the erosion
    evolved and stop there: do not rank turbines for repair, do not recommend
    what to fix first, do not bring up repair methods, costs or planning unless
    the user asked about them. Those questions have their own tools —
    prioritize_campaign_repair for what to repair first, aep_loss_on_a_site_for_each_turbine
    for the energy at stake. Wait for the user to raise the subject.

    A change is only reported as significant when the eroded length varies by
    more than 5%. Below that, the difference cannot be told apart from
    measurement variation, and the blade is reported as stable.

    Erosion never shrinks: it can only appear, grow, stay as it is, or be
    repaired. When previous_total_eroded_length is larger than
    total_eroded_length, that is a difference between the two measurements, not a
    physical change — report it as unchanged, and never describe the erosion as
    having shrunk, reduced, receded, healed or improved. The same holds for each
    eroded area.

    Args:
        inspection_id: The campaign to look at. Required — this tool never
            picks a campaign on its own. If the user did not name one, resolve it
            first: use the campaign from the page context when there is one, or
            ask the user which campaign they mean.
        turbine_id: The turbine to analyse. Give either this or site_id, not
            both. Use it when the user asks about one named turbine.
        site_id: The site to analyse. Every turbine of the park is covered, even
            those inspected in a different batch than inspection_id — each one
            is compared using its own campaign. Use it for questions about a
            whole park: "which turbines are worst on this site", "how did erosion
            progress here".
        previous_inspection_id: Optional. The campaign to compare against.
            When omitted, each turbine is compared with the campaign right before
            its own — which may differ from one turbine to another when a site
            was inspected in several batches. This is the normal case; pass it
            only when the user asks to compare with one specific older campaign
            for everything.
        blade: Optional. Restricts the answer to one blade, "A", "B" or "C".
        include_areas: Optional, default True. Set False to get only the blade
            totals and skip the zone-by-zone breakdown. On a whole site, prefer
            False unless the user really wants zone detail — the full breakdown
            for 40 turbines is a very long answer.

    Returns:
        One entry per turbine, each with its id and name, the campaign it was
        read from, the previous campaign it was compared against, and one entry
        per eroded blade:
        - total_eroded_length and previous_total_eroded_length, in meters along
          the leading edge, plus the delta and the percentage of change
        - status: new (no erosion before), grown, stable, or repaired (erosion
          before, none now)
        - eroded_areas_count: how many separate eroded zones, then and now. A
          count that drops while the length grows means zones merged, not that
          erosion receded
        - laminate_length: how much of the erosion reached the laminate rather
          than only the coat. This is the depth of the erosion, and it drives the
          repair method
        - spreads_to_pressure_side and spreads_to_suction_side, with their
          previous values: erosion spreading sideways off the leading edge is a
          change of nature, not just of size
        When include_areas is True, each blade also carries eroded_areas: one
        entry per zone with its radius range, eroded length then and now, deepest
        damaged part, max severity, WIND index, and how far it spreads onto each
        side. Zones are matched across campaigns by radius overlap, so treat
        their individual status as indicative and the blade total as the answer.
        growth_was_predicted marks a zone whose WIND index had already forecast
        the worsening.

        A blade with no erosion in either campaign does not appear, and neither
        does a turbine with no eroded blade at all — an absent turbine is a
        healthy one. Two lists say what was left out and why:
        turbines_never_inspected (no campaign at all at that date) and
        turbines_without_previous (nothing earlier to compare with). Both carry
        the turbine id and name, so they can be named to the user.
    '''
    print("TOOL_CALL analyse_erosion_evolution", inspection_id, turbine_id, site_id, previous_inspection_id, blade, include_areas)

    if not turbine_id and not site_id:
        return "ERROR: give either turbine_id or site_id"
    if turbine_id and site_id:
        return "ERROR: give turbine_id or site_id, not both"

    # ------------------------------------------------------------------ #
    # 1. Turbines concernées, et campagne courante de chacune
    #
    # Mode site : on part de TOUTES les turbines du parc, pas du périmètre de
    # l'inspection demandée — un site inspecté en deux lots resterait
    # sinon à moitié couvert.
    # ------------------------------------------------------------------ #
    if turbine_id:
        turbine_ids = [turbine_id]
        current_by_turbine = {turbine_id: inspection_id}
        never_inspected = []
    else:
        turbine_ids = resolve_site_turbine_ids(site_id)
        if not turbine_ids:
            return "ERROR: no turbine found for this site"
        current_by_turbine = resolve_current_by_turbine(turbine_ids, inspection_id)
        never_inspected = [tid for tid in turbine_ids if tid not in current_by_turbine]
        turbine_ids = [tid for tid in turbine_ids if tid in current_by_turbine]
    if not turbine_ids:
        return "ERROR: none of the turbines of this site was inspected at that date"

    if previous_inspection_id is not None:
        previous_by_turbine = {tid: previous_inspection_id for tid in turbine_ids}
    else:
        
        previous_by_turbine = resolve_previous_by_turbine(current_by_turbine)

    without_previous = [tid for tid in turbine_ids if tid not in previous_by_turbine]
    turbine_ids = [tid for tid in turbine_ids if tid in previous_by_turbine]

    if not turbine_ids:
        return ("ERROR: no earlier inspection campaign with damages for these turbines, "
                "there is nothing to compare with")

    # ------------------------------------------------------------------ #
    # 2. Chargement groupé : une requête par inspection distincte, dans
    #    les deux sens. Sur un site classique cela fait 2 requêtes, pas 2xN.
    # ------------------------------------------------------------------ #
    current_data_by_inspection = {}
    for current_id in set(current_by_turbine[tid] for tid in turbine_ids):
        concerned = [tid for tid in turbine_ids if current_by_turbine[tid] == current_id]
        current_data_by_inspection[current_id] = fetch_erosion_data_bulk(
            current_id, concerned)

    previous_data_by_inspection = {}
    for previous_id in set(previous_by_turbine[tid] for tid in turbine_ids):
        concerned = [tid for tid in turbine_ids if previous_by_turbine[tid] == previous_id]
        previous_data_by_inspection[previous_id] = fetch_erosion_data_bulk(
            previous_id, concerned)

    # ------------------------------------------------------------------ #
    # 3. Comparaison
    # ------------------------------------------------------------------ #
    turbines_payload = compare_turbines(
        turbine_ids, current_by_turbine, previous_by_turbine,
        current_data_by_inspection, previous_data_by_inspection,
        blade, include_areas,
    )

    # Le nom de la turbine ne sort pas des requêtes de dommages : une seule
    # requête ici, pour les turbines analysées et celles qui ont été écartées.
    turbine_names = resolve_turbine_names(
        turbine_ids + never_inspected + without_previous)

    for entry in turbines_payload:
        entry["turbine_name"] = turbine_names.get(entry["turbine_id"])

    def named(ids):
        return [{"turbine_id": tid, "turbine_name": turbine_names.get(tid)}
                for tid in ids]

    for turbine in turbines_payload:
        deltas = [b["length_delta"] for b in turbine["blades"]]
        turbine["turbine_mean_growth_in_meters"] = round(mean(deltas), 2)

    site_growth = [b["length_delta"] for t in turbines_payload for b in t["blades"]]

    BLADE_KEYS = [
        "blade", "status", "total_eroded_length", "length_delta",
        "spreads_to_pressure_side", "spreads_to_suction_side",
        "previously_spread_to_pressure_side", "previously_spread_to_suction_side",
    ]

    def slim_blade(blade):
        print(blade.keys())
        slim = {}
        for key in BLADE_KEYS:
            value = blade.get(key)
            slim[key] = round(value, 2) if isinstance(value, float) else value
        return slim
    
    

    turbines_payload = [
        (
            TurbineEvolution(
                turbine = Turbine(id=turbine["turbine_id"],
                                    name= turbine["turbine_name"],
                                    age=get_turbine_age(turbine["turbine_id"])),
                inspection=Inspection(id=turbine["planification_id"],
                                        published_date=get_publish_date(turbine["planification_id"])),
                
                previous_inspection=Inspection(id=turbine["previous_planification_id"],
                                                published_date=get_publish_date(turbine["previous_planification_id"])),
                mean_growth= round(mean(b["length_delta"] for b in turbine["blades"]), 2),
                blades=[BladeEvolution(
                                        blade_name=b["blade"],
                                        evolution_status=b["status"],
                                        evolution_length=round(b["length_delta"], 2),
                                        total_eroded_length=round(b["total_eroded_length"], 2),
                                        growth_percentage=round(b["growth_percentage"], 1),
                                        spread_to_pressure_side=b["spreads_to_pressure_side"],
                                        spread_to_suction_side=b["spreads_to_suction_side"],
                                        previously_spread_to_pressure_side=b["previously_spread_to_pressure_side"],
                                        previously_spread_to_suction_side=b["previously_spread_to_suction_side"],
                                    ) for b in turbine["blades"]
                        ],
            )
        )
        for turbine in turbines_payload
        if turbine["blades"]
    ]
    
    if site_id:
        payload = SiteEvolution(
            site= Site(id=site_id,name=get_site_name(site_id)),
            turbines= turbines_payload,
            site_mean_growth_in_meters= round(mean(site_growth), 2) if site_growth else None,
            site_median_growth_in_meters=round(median(site_growth), 2) if site_growth else None,
            turbines_never_inspected=[
                Turbine(id=tid, name=turbine_names.get(tid)) for tid in never_inspected],
            turbines_without_previous=[
                Turbine(id=tid, name=turbine_names.get(tid)) for tid in without_previous],
        )
    else:
        payload = turbines_payload

    resp = json.dumps(payload, default=str); print(resp)
    return resp


@tool
def get_inspection_erosion_details(
site_id: int,
inspection_id: int,
turbine_ids: list[int] | None = None
) -> str:
    """Give erosion details for a site. 
    Details are erosion area size and spreading on Pressure Face and Suction Face, from Leading Edge.

    Use this tool if the user ask for details or a diagnostic on the erosion of a site.
    
    Args:
        site_id: Site for which we want to know erosion details.
        inspection_id: Inspection for which we want to know erosion details.
        turbine_ids: Restrict to specific turbines
        
    Returns:
        A list of TurbineErosionDetails, one per turbine, with erosion details by blade.
    """
    print("TOOL_CALL get_site_erosion_details", site_id, inspection_id, turbine_ids)
    
    ### Ensure to get only concerned turbines
    
    TW_DB_CURSOR.execute(" \
        SELECT turbines.id, turbines.name \
        FROM planifications \
        JOIN asset_scope ON asset_scope.planification_id = planifications.id \
        JOIN turbines ON turbines.id = asset_scope.turbine_id \
        JOIN sites ON sites.id = turbines.site_id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = sites.id \
        LEFT OUTER JOIN fir_records \
        ON fir_records.planification_id = asset_scope.planification_id AND fir_records.turbine_id = asset_scope.turbine_id \
        WHERE planifications.deleted_at IS NULL AND asset_scope.deleted_at IS NULL \
        AND planifications.id = %s AND (fir_records.id IS NULL OR fir_records.deleted_at IS NOT NULL) \
    ", (GLOBAL_INFOS['CURRENT_COMPANY_ID'], inspection_id, ))
    
    concerned_turbines = {}
    insp_by_turbine = {} # Needed for get_current_data_for_turbines
    for row in TW_DB_CURSOR:
        turbine_id, turbine_name = row
        if not turbine_ids or turbine_id in turbine_ids:
            concerned_turbines[turbine_id] = Turbine(id=turbine_id, name=turbine_name)
            insp_by_turbine[turbine_id] = inspection_id
    
    turbines_payload = get_current_data_for_turbines(
        sorted(concerned_turbines), 
        insp_by_turbine,
        {inspection_id: fetch_erosion_data_bulk(inspection_id, sorted(concerned_turbines))},
        None, True
    )
    
    turbine_details = []
    for turbine_erosion_data in turbines_payload:

        blade_details = {}
        for blade_erosion_data in turbine_erosion_data['blades']:
            
            eroded_areas = []
            for eroded_area in blade_erosion_data['eroded_areas']:

                if eroded_area['spread_pressure_side'] == 0.0 and eroded_area['spread_suction_side'] == 0.0:
                    spreading = "Only on the LE"
                elif eroded_area['spread_pressure_side'] > 0.0:
                    spreading = "Spread on PS " + str(round(eroded_area['spread_pressure_side'], 2)) + "m"
                elif eroded_area['spread_suction_side'] > 0.0:
                    spreading = "Spread on SS " + str(round(eroded_area['spread_suction_side'], 2)) + "m"
                else:
                    spreading = "Spread on both PS " + str(round(eroded_area['spread_pressure_side'], 2)) + "m and SS" + str(round(eroded_area['spread_suction_side'], 2)) + "m"
                
                eroded_areas.append(ErosionAreaDetails(
                    severity=eroded_area['max_severity'],
                    depth=eroded_area['deepest_part'],
                    length=round(eroded_area['eroded_length'], 2),
                    radius_start=round(eroded_area['radius_start'], 2),
                    radius_end=round(eroded_area['radius_end'], 2),
                    spreading=spreading
                ))
            
            blade_details[blade_erosion_data['blade']] = BladeErosionDetails(
                total_length=round(blade_erosion_data['total_eroded_length'], 2),
                laminate_length=round(blade_erosion_data['laminate_length'], 2),
                eroded_areas=eroded_areas
            )
        
        print("DEBUG", blade_details)
        turbine_details.append(TurbineErosionDetails(
            turbine=concerned_turbines[turbine_erosion_data['turbine_id']],
            a_details=blade_details['A'] if 'A' in blade_details else 'No erosion',
            b_details=blade_details['B'] if 'B' in blade_details else 'No erosion',
            c_details=blade_details['C'] if 'C' in blade_details else 'No erosion'
        ))
        
    resp = str(turbine_details); print(resp)
    return resp
    
@tool
def get_inspection_crack_details(site_id: int,
                                 inspection_id: int,
                                 turbine_ids: list[int] | None = None) -> str:
    """Give crack details for a site at a given inspection.
 
    Use this when the user asks for details or a diagnostic on the cracks of a
    site: where they are, how long, how deep, how severe. This describes ONE
    campaign — for how cracks changed since the previous one, use
    analyse_crack_evolution instead.
 
    Args:
        site_id: Site for which we want the crack details.
        inspection_id: Inspection for which we want the crack details.
        turbine_ids: Optional. Restricts the answer to these turbines.
 
    Returns:
        One entry per turbine that has at least one crack, with its blades and,
        for each crack: its face, its position along the blade in meters, its
        length, how deep it goes (Coat, Laminate, Bonding line, Tip end), its
        severity and its WIND index.
        A turbine or a blade with no crack does not appear at all — an absent
        turbine is a healthy one, not missing data.
        Cracks are grouped: several overlapping vertical cracks are reported as
        one, so damage_id identifies the group and is not stable across campaigns.
    """
    print("TOOL_CALL get_inspection_crack_details", site_id, inspection_id, turbine_ids)
 
    # ------------------------------------------------------------------ #
    # 1. Turbines réellement inspectées
    #
    # Le LEFT JOIN sur fir_records écarte les turbines dont l'inspection a
    # échoué : elles sont dans le périmètre mais n'ont pas de relevé.
    # ------------------------------------------------------------------ #
    TW_DB_CURSOR.execute(
        "SELECT turbines.id, turbines.name "
        "FROM planifications "
        "JOIN asset_scope ON asset_scope.planification_id = planifications.id "
        "JOIN turbines ON turbines.id = asset_scope.turbine_id "
        "JOIN sites ON sites.id = turbines.site_id "
        "JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites "
        "  ON _societes_sites.site_id = sites.id "
        "LEFT OUTER JOIN fir_records "
        "  ON fir_records.planification_id = asset_scope.planification_id "
        " AND fir_records.turbine_id = asset_scope.turbine_id "
        "WHERE planifications.deleted_at IS NULL AND asset_scope.deleted_at IS NULL "
        "  AND planifications.id = %s AND sites.id = %s "
        "  AND (fir_records.id IS NULL OR fir_records.deleted_at IS NOT NULL)",
        (GLOBAL_INFOS["CURRENT_COMPANY_ID"], inspection_id, site_id),
    )
 
    turbines_by_id = {}
    for turbine_id, turbine_name in TW_DB_CURSOR.fetchall():
        if not turbine_ids or turbine_id in turbine_ids:
            turbines_by_id[turbine_id] = Turbine(id=turbine_id, name=turbine_name)
 
    if not turbines_by_id:
        return "ERROR: no inspected turbine found for this site and this inspection"
 
    # ------------------------------------------------------------------ #
    # 2. Fissures, en une seule requête pour tout le site
    # ------------------------------------------------------------------ #
    inspected_turbine_ids = sorted(turbines_by_id)
    crack_data = fetch_crack_data_bulk(inspection_id, inspected_turbine_ids)
 
    turbines_with_cracks = get_cracks_for_turbines(inspected_turbine_ids, crack_data)
 
    if not turbines_with_cracks:
        return "No crack found on this site at this inspection."
 
    # ------------------------------------------------------------------ #
    # 3. Mise en forme
    # ------------------------------------------------------------------ #
    turbine_details = []
 
    for turbine in turbines_with_cracks:
        blade_details = []
        turbine_cracks_count = 0
 
        for blade in turbine["blades"]:
            cracks = [
                CrackDetails(
                    damage_id=crack["damage_id"],
                    face=crack["face"],
                    radius=round(crack["radius"], 2) if crack["radius"] is not None else None,
                    length=round(crack["size"], 3) if crack["size"] is not None else None,
                    measured_as=crack["measured_as"],
                    orientation=crack["orientation"],
                    shape=crack["shape"],
                    severity=crack["severity"],
                    depth=crack["damaged_part"],
                    wind=crack["wind"],
                    measure_source=crack["measure_source"],
                )
                for crack in blade["cracks"]
            ]
 
            severities = [c.severity for c in cracks if c.severity is not None]
 
            blade_details.append(BladeCrackDetails(
                blade=blade["blade"],
                cracks_count=len(cracks),
                max_severity=max(severities) if severities else None,
                cracks=cracks,
            ))
            turbine_cracks_count += len(cracks)
 
        turbine_details.append(TurbineCrackDetails(
            turbine=turbines_by_id[turbine["turbine_id"]],
            cracks_count=turbine_cracks_count,
            blades=blade_details,
        ))
 
    # Les turbines les plus fissurées en tête : c'est presque toujours la question.
    turbine_details.sort(key=lambda t: t.cracks_count, reverse=True)
 
    return json.dumps([t.model_dump() for t in turbine_details], default=str)
