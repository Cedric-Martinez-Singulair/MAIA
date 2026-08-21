from get_ids_mcp import *
from general_mcp import *
from damage_mcp import *

MAIA_TOOLS = [
    get_damage_type_ids,
    get_country_ids_by_name,
    get_turbine_model_ids_by_name,
    get_sites,
    
    count_damage_on_turbines,
    count_damage_on_a_site_turbines,
    estimate_repair_cost,
    get_individual_damage_infos,
    estimate_damage_repair_frequency,
    
    get_wind_index_explanation,
    what_is_new
]