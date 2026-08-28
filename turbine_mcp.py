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
    
    elif avg_type == 'country':
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
            res_turbine_count = row
        else:
            avg_key, turbine_count = row
            if avg_key is not None: 
                res_turbine_count[avg_key] = turbine_count
    
    print("--- RES", res_turbine_count)
    return str(res_turbine_count)