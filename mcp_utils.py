from get_ids_mcp import *
from general_mcp import *
from damage_mcp import *
from turbine_mcp import *

MAIA_TOOLS = [
    get_damage_type_ids,
    get_country_ids_by_name,
    get_turbine_model_ids_by_name,
    get_sites,
    get_site_damage_ids,
    
    get_damage_path,
    count_damage_on_turbines,
    count_damage_on_a_site_turbines,
    estimate_repair_cost,
    get_individual_damage_infos,
    estimate_damage_repair_frequency,
    aep_loss_on_a_site_for_each_turbine,
    aep_loss_on_a_turbine,
    analyse_crack_evolution,
    analyse_erosion_evolution,
    
    count_turbines,
    
    get_wind_index_explanation,
    what_is_new
]