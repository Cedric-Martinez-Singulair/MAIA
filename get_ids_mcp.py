from langchain_core.tools import tool
from maia_utils import *

from collections import defaultdict

@tool
def get_damage_type_ids() -> str:
    '''List every damage type in Turbinewatch, with its id.
    Use this tool if you need to know a damage type id.
    
    Returns:
        A dict where the keys are the damage types, and the values the damage type ids.'''
    print("TOOL_CALL get_damage_type_ids")
    
    TW_DB_CURSOR.execute(" \
        SELECT id, label_en FROM defect_types \
        WHERE deleted_at IS NULL \
    ")
    
    damage_type_ids = {}
    for row in TW_DB_CURSOR:
        damage_id, label_en = row
        damage_type_ids[label_en] = damage_id
    
    return str(damage_type_ids)

@tool
def get_country_ids_by_name() -> str:
    '''List every country in Turbinewatch, with its id.
    Use this tool if you need to know a country id.
    
    Returns:
        A dict where the keys are the country names, and the values the country ids.'''
    print("TOOL_CALL get_country_ids_by_name")
    
    TW_DB_CURSOR.execute(" \
        SELECT id, label_en FROM countries \
        WHERE deleted_at IS NULL \
    ")
    
    country_ids_by_name = {}
    for row in TW_DB_CURSOR:
        country_id, label_en = row
        country_ids_by_name[label_en] = country_id
    
    return str(country_ids_by_name)

@tool
def get_turbine_model_ids_by_name() -> str:
    '''List every turbine model in Turbinewatch, with its id.
    Use this tool if you need to know a turbine model id.
    
    Returns:
        A dict where the keys are the model names, and the values the model ids.'''
    print("TOOL_CALL get_turbine_model_ids_by_name")
    
    TW_DB_CURSOR.execute(" \
        SELECT DISTINCT models.id, models.name FROM models \
        JOIN turbines ON turbines.model_name = models.id \
        WHERE models.deleted_at IS NULL AND turbines.deleted_at IS NULL \
    ")
    
    model_ids_by_name = {}
    for row in TW_DB_CURSOR:
        model_id, model_name = row
        model_ids_by_name[model_name] = model_id
    
    return str(model_ids_by_name)

@tool
def get_sites(country_id: int | None = None, model_id: int | None = None,) -> str:
    '''Can be used to list sites and also to know their ids.
    
    Args:
        country_id: Restrict the result to the sites inside a specific country.
        model_id:  Restrict the result to the sites with turbines of a specific model.
    
    Returns: 
        A list of Site objects, each with its id and its name.'''
    print("TOOL_CALL list_sites", country_id, model_id)
    
    req_str = " \
        SELECT DISTINCT sites.id, sites.name FROM sites \
        JOIN turbines ON turbines.site_id = sites.id \
        WHERE sites.deleted_at IS NULL AND turbines.deleted_at IS NULL \
    "
    
    req_optional_params = []
    if country_id is not None:
        req_str += "AND sites.country_id = %s "; req_optional_params.append(country_id)
    if model_id is not None:
        req_str += "AND turbines.model_name = %s "; req_optional_params.append(model_id)
    
    print("--- REQ", req_str, req_optional_params)
    if len(req_optional_params) == 0: TW_DB_CURSOR.execute(req_str)
    elif len(req_optional_params) == 1: TW_DB_CURSOR.execute(req_str, (req_optional_params[0], ))
    elif len(req_optional_params) == 2: TW_DB_CURSOR.execute(req_str, (req_optional_params[0], req_optional_params[1], ))
    
    sites = []
    for row in TW_DB_CURSOR:
        site_id, site_name = row
        sites.append(Site(id=site_id, name=site_name))
    
    print("--- RES", sites)
    return str(sites)

#TODO mettre à jour la description
DamageAggregation = Literal["damage_type", "blade_component", "severity"]
@tool
def get_site_damage_ids(
site_id : int, 
location_precision: Literal["turbine", "blade", "face"],
damage_aggregations: list[DamageAggregation] = [],
planification_id: int | None = None,
damage_type_id: int | None = None, severities: list[int] | None = None,
turbine_ids: list[int] | None = None,
) -> str:
    '''List the ids of every damage reported on a given site on each individual turbine.

    Use this to know the ids of damages on a specific site.
    Prioritize using this tool with a result restricted to one planification.
    Prioritize using the restrictive parameters, damage_type_id, severities, turbine_ids.
    You can get damage_type_id with the tool get_damage_type_ids.

    Args:
        site_id: The site whose damages are listed.
        location_precision: Specify how finely damages are located in the result: 
            one row per turbine, per blade, or per blade face.
        damage_aggregations: Specify which characteristics split damages into separate
            counts. Pass an empty list for a single total per location. "blade_component"
            groups by the component of the blade affected (coat, laminate, lightning receptor...),
            which is independent of location_precision.
        planification_id: Restrict the result to a single planification. If omitted, every planification recorded for this site is returned.
        damage_type_id: Restrict to one damage type.
        severities: Restrict to some severities. Can be combined with damage_type_id.
        turbine_ids: Restricts the result to some turbines. When left empty, every damaged turbine of the planification is returned.

    Returns:
        One PlanificationDamageListResult per campaign, each holding the planification
        date (YYYY-mm-dd) and one row per location at the requested precision. Every row
        carries the turbine name, the blade and face when
        applicable, the value of each requested aggregation, and the damage ids.'''
    print("TOOL_CALL get_site_damage_ids", site_id, location_precision, damage_aggregations, planification_id, damage_type_id, severities, turbine_ids)
    
    req_params = [site_id]
    req_str = "SELECT incident_records.id AS damage_id, planifications.id AS planif_id, CAST(planifications.date AS VARCHAR) AS planif_date, turbines.id AS turbine_id, turbines.name AS turbine_name, "
    
    if location_precision == 'blade': req_str += "components_turbines.name AS blade, "
    elif location_precision == 'face': req_str += "components_turbines.name AS blade, parts.label_en AS face, "
    if 'damage_type' in damage_aggregations: req_str += "defect_types.id AS damage_type_id, defect_types.label_en AS damage_type, "
    if 'blade_component' in damage_aggregations: req_str += "components.id AS blade_component_id, components.label_en AS blade_component, "
    if 'severity' in damage_aggregations: req_str += "incident_records.criticality_id AS severity, "
    
    req_str = req_str[:-2] # Remove last comma
    
    req_str += " \
        FROM incident_records \
        JOIN planifications ON planifications.id = incident_records.planification_id \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
    "
    
    if location_precision == 'blade': req_str += "JOIN components_turbines ON components_turbines.id = incident_records.component_turbine_id "
    elif location_precision == 'face': 
        req_str += "JOIN components_turbines ON components_turbines.id = incident_records.component_turbine_id "
        req_str += "JOIN parts ON parts.id = incident_records.part_id "
    if 'damage_type' in damage_aggregations: req_str += "JOIN defect_types ON defect_types.id = incident_records.defect_type_id "
    if 'blade_component' in damage_aggregations: req_str += "JOIN components ON components.id = incident_records.component_id "
    # No need to JOIN for severity
    
    req_str += " \
        WHERE incident_records.dismissed = FALSE AND incident_records.decision_id != 0 AND incident_records.deleted_at IS NULL AND turbines.deleted_at IS NULL AND planifications.deleted_at IS NULL \
            AND turbines.site_id = %s \
    "
    
    if planification_id is not None:
        req_str += "AND incident_records.planification_id = %s "; req_params.append(planification_id)
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
    
    req_str += " ORDER BY planifications.id, turbines.id, "
    
    if location_precision == 'blade': req_str += "components_turbines.id, "
    elif location_precision == 'face': req_str += "components_turbines.id, parts.id, "
    if 'damage_type' in damage_aggregations: req_str += "defect_types.id, "
    if 'blade_component' in damage_aggregations: req_str += "components.id, "
    if 'severity' in damage_aggregations: req_str += "incident_records.criticality_id, "
    
    req_str = req_str[:-2] # Remove the last comma
        
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    damage_id_lists = []
    found_planification_id = []
    found_planif_turbine_id = []
    charac_comb_found = []
    
    if damage_type_id is None and severities is None: damage_list_filter = "Count all damages"
    elif damage_type_id is not None and severities is None: damage_list_filter = "Only count damage_type_id = " + str(damage_type_id)
    elif damage_type_id is None and severities is not None: damage_list_filter = "Only count severities in " + str(severities)
    elif damage_type_id is not None and severities is not None: damage_list_filter = "Only count damage_type_id = " + str(damage_type_id) + " and severity = " + str(severity)
    
    req_columns = [col[0] for col in TW_DB_CURSOR.description]
    for row in TW_DB_CURSOR:
        record = dict(zip(req_columns, row))
        if 'severity' in record and record['severity'] == 6: record['severity'] = 0
        
        if record['planif_id'] not in found_planification_id:
            found_planification_id.append(record['planif_id'])
            damage_id_lists.append(PlanificationDamageListResult(
                planification=Planification(id=record['planif_id'], date=record['planif_date']),
                turbine_damage_id_lists=[]
            ))
            found_planif_turbine_id = []
            charac_comb_found = []
        
        if record['turbine_id'] not in found_planif_turbine_id:
            found_planif_turbine_id.append(record['turbine_id'])
            damage_id_lists[-1].turbine_damage_id_lists.append(TurbineDamageListResult(
                turbine=Turbine(id=record['turbine_id'], name=record['turbine_name']),
                damage_list_filter=damage_list_filter,
                damage_id_lists=[]
            ))
            charac_comb_found = []
        
        blade = record.get('blade'); face = record.get('face'); severity = record.get('severity')
        blade_component = BladeComponent(id=record['blade_component_id'], name=record['blade_component']) if 'blade_component_id' in record else None
        damage_type = DamageType(id=record['damage_type_id'], name=record['damage_type']) if 'damage_type_id' in record else None
        charac_comb = str(blade) + "_" + str(face) + "_" + str(severity) + "_" + str(record.get('blade_component_id')) + "_" + str(record.get('damage_type_id')) + "_"
        
        if charac_comb not in charac_comb_found:
            charac_comb_found.append(charac_comb)
            damage_id_lists[-1].turbine_damage_id_lists[-1].damage_id_lists.append(DamageListResult(
                blade=record.get('blade'),
                face=record.get('face'),
                blade_component=blade_component,
                damage_type=damage_type,
                severity=record.get('severity'),
                damage_ids=[]
            ))
            
        damage_id_lists[-1].turbine_damage_id_lists[-1].damage_id_lists[-1].damage_ids.append(record['damage_id'])
    
    print("--- RES", damage_id_lists)
    return str(damage_id_lists)