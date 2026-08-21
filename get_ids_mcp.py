from langchain_core.tools import tool
from maia_utils import *

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
        
    req_str += "LIMIT 100 "
    
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