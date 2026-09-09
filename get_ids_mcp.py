from langchain_core.tools import tool
from maia_utils import *

from collections import defaultdict

@tool
def get_inspection_type_ids() -> str:
    '''List every inspection type in Turbinewatch, with its id.
    Use this tool if you need to know a inspection type id.
    
    Returns:
        A dict where the keys are the inspection types, and the values the inspection type ids.'''
    print("TOOL_CALL get_inspection_type_ids")
    
    req_str = " \
        SELECT id, label FROM controles \
    "
    
    TW_DB_CURSOR.execute(req_str)
    
    inspection_type_ids = {}
    for row in TW_DB_CURSOR:
        inspection_type_id, label_en = row
        inspection_type_ids[label_en] = inspection_type_id
    
    resp = str(inspection_type_ids); print("-- RES", resp)
    return resp

@tool
def get_damage_type_ids() -> str:
    '''List every damage type in Turbinewatch, with its id.
    Use this tool if you need to know a damage type id.
    
    Returns:
        A dict where the keys are the damage types, and the values the damage type ids.'''
    print("TOOL_CALL get_damage_type_ids")

    req_str = " \
        SELECT DISTINCT defect_type_id, defect_types.label_en \
        FROM components_criticalities_defect_types \
        JOIN defect_types ON defect_types.id = components_criticalities_defect_types.defect_type_id \
        WHERE component_id IN ( \
            SELECT DISTINCT component_id \
            FROM (SELECT DISTINCT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites \
            JOIN (SELECT DISTINCT site_id, model_name FROM turbines WHERE deleted_at IS NULL) _turbines ON _turbines.site_id = _societes_sites.site_id \
            JOIN (SELECT DISTINCT make_id, id AS model_id FROM models WHERE deleted_at IS NULL) _models ON _models.model_id = _turbines.model_name \
            JOIN ( \
                SELECT _models.id AS model_id, _components.id AS component_id \
                FROM ( \
                    SELECT id, label_en AS LABEL, LOWER(color) AS color, abr, type_id, LOWER(analyze_color) AS analyze_color \
                    FROM components \
                    WHERE id != 0 AND type_id = 1 AND deleted_at IS NULL \
                    ORDER BY id  \
                ) _components \
                LEFT JOIN (SELECT make_id, id FROM models WHERE deleted_at IS NULL AND NAME IS NOT NULL) _models ON TRUE \
                LEFT JOIN (SELECT * FROM components_by_models) _components_by_models ON (_components_by_models.make_id = _models.make_id) AND _components_by_models.component_id = _components.id \
                WHERE _components_by_models.make_id IS NULL AND model_id IS NULL \
                ORDER BY _models.id, _components.id \
            ) _ \
            ON _.model_id = _models.model_id \
            ORDER BY component_id \
        ) \
    "
    
    print("-- REQ", req_str, GLOBAL_INFOS['CURRENT_COMPANY_ID'])
    TW_DB_CURSOR.execute(req_str, (GLOBAL_INFOS['CURRENT_COMPANY_ID'], ))
    
    damage_type_ids = {}
    for row in TW_DB_CURSOR:
        damage_type_id, label_en = row
        damage_type_ids[label_en] = damage_type_id

    resp = str(damage_type_ids); print("-- RES", resp)
    return resp

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
    
    resp = str(country_ids_by_name); print("-- RES", resp)
    return resp

@tool
def get_turbine_model_ids_by_name() -> str:
    '''List every turbine model in Turbinewatch, with its id.
    Use this tool if you need to know a turbine model id.
    
    Returns:
        A dict where the keys are the model names, and the values the model ids.'''
    print("TOOL_CALL get_turbine_model_ids_by_name")
    
    TW_DB_CURSOR.execute(" \
        SELECT DISTINCT models.id, models.name \
        FROM models \
        JOIN turbines ON turbines.model_name = models.id \
        JOIN sites ON sites.id = turbines.site_id \
        JOIN societes_sites ON societes_sites.site_id = sites.id \
        WHERE models.deleted_at IS NULL AND turbines.deleted_at IS NULL \
        AND societes_sites.societe_id = %s \
    ", (GLOBAL_INFOS['CURRENT_COMPANY_ID'], ))
    
    model_ids_by_name = {}
    for row in TW_DB_CURSOR:
        model_id, model_name = row
        model_ids_by_name[model_name] = model_id
    
    resp = str(model_ids_by_name); print("-- RES", resp)
    return resp

@tool
def get_sites(country_id: int | None = None, model_id: int | None = None, site_name: str | None = None) -> str:
    '''Can be used to list sites and also to know their ids.
    
    Args:
        country_id: Restrict the result to the sites inside a specific country.
        model_id: Restrict the result to the sites with turbines of a specific model.
        site_name: Restrict to site with this name.
    
    Returns: 
        A list of Site objects, each with its id and its name.'''
    print("TOOL_CALL get_sites", country_id, model_id, site_name)
    
    if (site_name is not None and (len(site_name) > 20 or '%' in site_name)):
        resp = "Site name too long."
        print("--- RES", resp); return resp
    
    req_str = " \
        SELECT DISTINCT sites.id, sites.name \
        FROM sites \
        JOIN turbines ON turbines.site_id = sites.id \
        JOIN societes_sites ON societes_sites.site_id = sites.id \
        WHERE sites.deleted_at IS NULL AND turbines.deleted_at IS NULL \
        AND societes_sites.societe_id = %s \
    "
    
    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID']]
    
    if country_id is not None:
        req_str += "AND sites.country_id = %s "; req_params.append(country_id)
    if model_id is not None:
        req_str += "AND turbines.model_name = %s "; req_params.append(model_id)
    if site_name is not None:
        req_str += "AND SOUNDEX(sites.name) = SOUNDEX(%s) "; req_params.append(site_name)
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    sites = []
    for row in TW_DB_CURSOR:
        site_id, site_name = row
        sites.append(Site(id=site_id, name=site_name))
    
    if len(sites) > 100:
        resp = "There is too much data to analyse.\n"
        if country_id is None:
            resp += "You can propose to the user to specify a country.\n"
        if model_id is None:
            resp += "You can propose to the user to specify a turbine model.\n"
        if site_name is None:
            resp += "You can propose to the user to specify a site name.\n"
        print("--- RES", resp)
        return str(resp)

    resp = str(sites); print("-- RES", resp)
    return resp

DamageAggregation = Literal["damage_type", "blade_component", "severity"]
@tool
def get_site_damage_ids(
site_id : int, 
location_precision: Literal["turbine", "blade", "face", "radius"],
damage_aggregations: list[DamageAggregation] = [],
inspection_id: int | None = None,
damage_type_id: int | None = None, severities: list[int] | None = None,
turbine_ids: list[int] | None = None,
) -> str:
    '''Return individual damage ids for follow-up tools (repair frequency, damage details, images).

    Use this to know the ids of damages on a specific site.
    Do NOT use this when the user asks to "show" or "list" damages on a site.
    For that, use count_damage_on_a_site_turbines instead.
    If location_precision is radius you must filter on a damage type with damage_type_id.
    
    Prioritize using this tool with a result restricted to one inspection.
    Prioritize using the restrictive parameters, damage_type_id, severities, turbine_ids.
    You can get damage_type_id with the tool get_damage_type_ids.

    Args:
        site_id: The site whose damages are listed.
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
        One InspectionDamageListResult per campaign, each holding the inspection
        date (YYYY-mm-dd) and one row per location at the requested precision. Every row
        carries the turbine name, the blade and face when
        applicable, the value of each requested aggregation, and the damage ids.'''
    print("TOOL_CALL get_site_damage_ids", site_id, location_precision, damage_aggregations, inspection_id, damage_type_id, severities, turbine_ids)
    
    req_str = "SELECT incident_records.id AS damage_id, planifications.id AS planif_id, CAST(planifications.date AS VARCHAR) AS planif_date, turbines.id AS turbine_id, turbines.name AS turbine_name, "
    
    if location_precision == 'blade': req_str += "components_turbines.name AS blade, "
    elif location_precision == 'face': req_str += "components_turbines.name AS blade, parts.label_en AS face, "
    elif location_precision == 'radius': req_str += "components_turbines.name AS blade, parts.label_en AS face, incident_records.radius, "
    if 'damage_type' in damage_aggregations: req_str += "defect_types.id AS damage_type_id, defect_types.label_en AS damage_type, "
    if 'blade_component' in damage_aggregations: req_str += "components.id AS blade_component_id, components.label_en AS blade_component, "
    if 'severity' in damage_aggregations: req_str += "incident_records.criticality_id AS severity, "
    
    req_str = req_str[:-2] # Remove last comma
    
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
    
    req_str += " ORDER BY planifications.id, turbines.id, "
    
    if location_precision == 'blade': req_str += "components_turbines.id, "
    elif location_precision == 'face': req_str += "components_turbines.id, parts.id, "
    elif location_precision == 'radius': req_str += "components_turbines.name, parts.label_en, incident_records.radius, "
    if 'damage_type' in damage_aggregations: req_str += "defect_types.id, "
    if 'blade_component' in damage_aggregations: req_str += "components.id, "
    if 'severity' in damage_aggregations: req_str += "incident_records.criticality_id, "
    
    req_str = req_str[:-2] # Remove the last comma
        
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    damage_id_lists = []
    found_inspection_id = []
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
        
        if record['planif_id'] not in found_inspection_id:
            found_inspection_id.append(record['planif_id'])
            damage_id_lists.append(InspectionDamageListResult(
                inspection=Inspection(
                    id=record['planif_id'], date=record['planif_date'], published_date=str(get_publish_date(record['planif_id']))
                ),
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
                radius=record.get('radius'),
                blade_component=blade_component,
                damage_type=damage_type,
                severity=record.get('severity'),
                damage_ids=[]
            ))
            
        damage_id_lists[-1].turbine_damage_id_lists[-1].damage_id_lists[-1].damage_ids.append(record['damage_id'])
    
    resp = str(damage_id_lists); print("-- RES", resp)
    return resp