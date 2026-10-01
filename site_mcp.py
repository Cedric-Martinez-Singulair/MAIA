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
    
    req_str = "SELECT sites.id,sites.name, sites.longitude,sites.latitude FROM sites WHERE sites.id = ANY (%s)"
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

@tool
def get_nearest_sites(ref_site_id: int, nb_nearest: int = 10, client_id: int | None = None) -> str:
    '''Give a list of the 10 nearest sites to the reference site
    
    Args:
        ref_site_id: Reference site for which we want to know the nearest other sites.
        client_id: Client Company id to filter on. Never guess this value.
    Returns:
        A list of NearbySite ordered from the nearest to the furthest
    '''
    print("TOOL_CALL get_nearest_sites", ref_site_id, nb_nearest, client_id)
    
    if nb_nearest > 100:
        resp = "nb_nearest must be < 100"; print(resp)
        return resp
    
    req_str = " \
        SELECT other_site.id, other_site.name, \
            ROUND((ST_Distance( \
                ST_MakePoint(ref_site.longitude::FLOAT, ref_site.latitude::FLOAT)::geography, \
                ST_MakePoint(other_site.longitude::FLOAT, other_site.latitude::FLOAT)::geography \
            ) / 1000)::NUMERIC, 1) AS distance_km \
        FROM sites ref_site \
        JOIN sites other_site ON other_site.id <> ref_site.id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = other_site.id \
        WHERE ref_site.id = %s \
            AND other_site.deleted_at IS NULL \
            AND other_site.latitude IS NOT NULL \
            AND other_site.longitude IS NOT NULL \
        ORDER BY distance_km \
        LIMIT %s \
    "
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] != 5 or client_id is None:
        req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID']]
    else:
        req_params = [client_id]
    req_params.append(ref_site_id); req_params.append(nb_nearest)
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    nearest_sites = []
    for row in TW_DB_CURSOR:
        site_id, site_name, distance = row
        nearest_sites.append(
            NearbySite(site=Site(id=site_id, name=site_name), distance=distance)
        )
    
    resp = TypeAdapter(list[NearbySite]).dump_json(nearest_sites).decode(); print("--- RES", resp)
    return resp

@tool
def get_manufacturer(site_id:int):
    '''
    Give the blades manufaturer of a given site
    
    Args:
        -site_id the id of a given site
    Returns:
        A list of TurbineModel
    '''
    req_params = []
    req_str="""  SELECT
    t.id AS turbine_id,
    m.name AS model_name,
    STRING_AGG(DISTINCT COALESCE(NULLIF(SUBSTRING(c.blade_serial FROM '^[^0-9]+'), ''), '(aucun)'), ', ') AS manufacturer
    FROM turbines t
    JOIN models m ON m.id = t.model_name
    LEFT JOIN components_turbines_enercon c ON c.turbine_id = t.id
    WHERE t.site_id = %s
    GROUP BY t.id, m.name
    ORDER BY t.id;"""
    req_params.append(site_id)
    
    TW_DB_CURSOR.execute(req_str,req_params)
    
    result=[]
    for row in TW_DB_CURSOR:
        id,model,manufacturer = row
        result.append(TurbineModel(id=id,
                                     name=model,
                                     manufacturer=manufacturer))
    
    resp = TypeAdapter(list[TurbineModel]).dump_json(result).decode(); print("--- RES", resp) 
    
    ##TODO renvoyer une Turbine plutot qu'un TurbineModel comme ca on a le nom de la turbine
    
    return resp