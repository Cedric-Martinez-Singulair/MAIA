from __future__ import annotations

import psycopg2
import requests

from pydantic import BaseModel, Field
from typing import Literal

### TW access ###

TW_DB_PARAMS = dict(
    dbname="windwatch",
    user="windwatch",
    password="windwatch",
    host="10.11.40.121",
    port=5432,
)
        
# Compatibilité : tes tools existants continuent de fonctionner tels quels.
tw_db_connection = psycopg2.connect(**TW_DB_PARAMS)
TW_DB_CURSOR = tw_db_connection.cursor()

### GLOBAL PARAMETERS ###

GLOBAL_INFOS = {'CURRENT_COMPANY_ID': None, 'CURRENT_COMPANY': None, 'CURRENT_PERSONA': None}

### Entity BaseModel classes ###

class BladeComponent(BaseModel):
    id: int = Field(description='Id of this blade part in the turbinewatch database')
    name: str = Field(description='Name of the wind turbine blade part')

class DamageType(BaseModel):
    id: int = Field(description='Id of this damage type in the turbinewatch database')
    name: str = Field(description='Describe the type of damage')

class TurbineModel(BaseModel):
    id: int = Field(description='Turbine model id, needed to identify the turbine model in the turbinewatch database')
    name: str = Field(description='Turbine model name (e.g. V90)')

class Turbine(BaseModel):
    id: int = Field(description='Turbine id, needed to identify the turbine in the turbinewatch database')
    name: str = Field(description='Turbine name (e.g. T01)')
    age: int | None = Field(default=None, description='Turbine age, since entry service, at a specific inspection')
    model: TurbineModel | None = Field(default=None, description='Turbine model')
    damages: list[Damage] | None = Field(default_factory=list, description='all the damages located on this turbine')
    longitude : float | None = Field(default=None,description='The longitude of the turbine')
    latitude : float |None= Field(default=None,description='The latitude of the turbine')

class Site(BaseModel):
    id: int = Field(description='Site id, needed to identify the site in the turbinewatch database')
    name: str = Field(description='Site name')
    turbines: list[Turbine] | None = Field(
        default_factory=list, description='Turbines belonging to this site',
    )
    longitude : float | None = Field(default=None, description='The longitude of the site')
    latitude : float | None= Field(default=None, description='The latitude of the site')

class Country(BaseModel):
    id: int = Field(description='Country id, needed to identify the country in the turbinewatch database')
    name: str = Field(description='Country name')
    sites: list[Turbine] | None = Field(default_factory=list, description='Sites located in this country')

class ClientCompany(BaseModel):
    id: int = Field(description='Company id, needed to identify the company in the turbinewatch database')
    name: str = Field(description='Name of the Singulair\'s client company')

class Inspection(BaseModel):
    id: int = Field(description='Inspection id, needed to identify the inspection in the turbinewatch database')
    date: str | None = Field(default=None, description='Inspection date (format YYYY-mm-dd)')
    published_date: str = Field(default=None, description='Inspection publication date (format YYYY-mm-dd)')
    site: Site | None = Field(default=None, description='Inspected site')
    requester: ClientCompany | None = Field(default=None, description='Client company who requested the inspection')

class Damage(BaseModel):
    id: int = Field(description='Id of this specific damage in the turbinewatch database')
    damaged_part: BladeComponent | None = Field(default=None, description='Component of the blade where this damage is located')
    damage_type: DamageType | None = Field(default=None, description='Type of this specific damage')
    site: Site | None = Field(default=None, description='Site where the damage is located')
    turbine: Turbine | None = Field(default=None, description='Turbine where the damage is located')
    blade: str | None = Field(default=None, description='Blade where the damage is located')
    face: str | None = Field(default=None, description='Face where the damage is located')
    radius: int | None = Field(default=None, description='Radius where the damage is located')
    inspection: Inspection | None = Field(default=None, description='Inspection at which the damage have been reported')
    wind:str | None =  Field(default=None,description="The WIND index value associated to the damage")

### Result BaseModel classes ###

# Count damages

class DamageCountResult(BaseModel):
    blade: str | None = Field(default=None, description='Blade where the counted damages are located')
    face: str | None = Field(default=None, description='Face where the counted damages are located')
    radius: int | None = Field(default=None, description='Radius where the counted damages are located')
    blade_component: BladeComponent | None = Field(default=None, description='Blade component where the counted damages are located')
    damage_type: DamageType | None = Field(default=None, description='Type of the counted damages')
    severity: int | None = Field(default=None, description='Severity of the counted damages')
    damage_count: int = Field(description='Count of damage')

class TurbineDamageCountResult(BaseModel):
    turbine: Turbine = Field(description='Turbine on which the damage count is')
    damage_count_filter: str = Field(description='Precise if the turbine damages are filtered before being count')
    damage_counts: list[DamageCountResult] = Field(
        default_factory=list, description='Counts of damage, corresponding to the damage_count_filter, on this turbine',
    )

class InspectionDamageCountResult(BaseModel):
    inspection: Inspection = Field(description='Inspection at which damage on turbines are counted')
    turbine_damage_counts: list[TurbineDamageCountResult] = Field(
        default_factory=list, description='Damage count on each turbine associated to the inspection'
    )

# List damages

class DamageListResult(BaseModel):
    blade: str | None = Field(default=None, description='Blade where the damages are located')
    face: str | None = Field(default=None, description='Face where the damages are located')
    radius: int | None = Field(default=None, description='Radius where the damages are located')
    blade_component: BladeComponent | None = Field(default=None, description='Blade component where the damages are located')
    damage_type: DamageType | None = Field(default=None, description='Type of the damages')
    severity: int | None = Field(default=None, description='Severity of the damages')
    damage_ids: list[int] = Field(
        default_factory=list, description='List of damage ids'
    )

class TurbineDamageListResult(BaseModel):
    turbine: Turbine = Field(description='Turbine on which the damage ids are listed')
    damage_list_filter: str = Field(description='Precise if the turbine damages are filtered before being listed')
    damage_id_lists: list[DamageListResult] = Field(
        default_factory=list, description='Lists of damage ids'
    )

class InspectionDamageListResult(BaseModel):
    inspection: Inspection = Field(description='Inspection at which the damage ids are listed')
    turbine_damage_id_lists: list[TurbineDamageListResult] = Field(
        default_factory=list, description='Damage ids list on each turbine associated to the inspection',
    )
    
# Erosion details

class ErosionAreaDetails(BaseModel):
    severity: int = Field(description='Maximum severity for this eroded area')
    depth: Literal['Coat', 'Laminate','Blade Tip','Paint'] = Field(description='Precise if the area is coat deep or laminate deep')
    length: float = Field(description='Erosion area length in meters')
    radius_start: float = Field(description='Starting erosion radius')
    radius_end: float = Field(description='Ending erosion radius')
    spreading: str = Field(description='Describe if the erosion is spreading on pressure side or suction side')

class BladeErosionDetails(BaseModel):
    total_length: float = Field(description='Total erosion length on this blade in meters')
    laminate_length: float = Field(description='Total length of eroded laminate on this blade in meters')
    eroded_areas: list[ErosionAreaDetails] = Field(
        default_factory=list, description='Eroded areas on this blade'
    )

class TurbineErosionDetails(BaseModel):
    turbine: Turbine = Field(description='Turbine on which erosion is detailed')
    a_details: BladeErosionDetails | Literal['No erosion'] = Field(description='Erosion details for blade A')
    b_details: BladeErosionDetails | Literal['No erosion'] = Field(description='Erosion details for blade B')
    c_details: BladeErosionDetails | Literal['No erosion'] = Field(description='Erosion details for blade C')

# Repair cost

class DamageGroupRepairCost(BaseModel):
    max_severity: int = Field(description='Maximum severity of this group of damage')
    damage_type: DamageType = Field(description='Damage type of this group of damage')
    damage_count: int = Field(description='Count of damage in this group')
    repair_cost: float = Field(description='Repair cost for this group of damage in €')
    explanation: str = Field(description='Precise how the repair cost have been calculated')

class TurbineRepairCost(BaseModel):
    total_repair_cost: float = Field(description='Total repair cost on this turbine for the asked damage types')
    turbine: Turbine = Field(description='Turbine on which are the damages for which we estimated the repair cost')
    damage_group_repair_cost: list[DamageGroupRepairCost] = Field(
        default_factory=list, description='List of group of damage for which we estimated the repair cost',
    )

class InspectionRepairCost(BaseModel):
    inspection: Inspection = Field(description='Inspection at which we estimated the repair costs')
    turbine_repair_costs: list[TurbineRepairCost] = Field(
        default_factory=list, description='List of turbines on which there are groups of damages for which we estimated the repair cost',
    )

# Repair Height

class DamageRepairHeight(BaseModel):
    part: str | None = Field(description="Damaged part")
    component: str | None = Field(description="Damaged component")
    defect_type: str | None = Field(description="Type of damage")
    severity: int | None = Field(description="Damage severity (<= 2 is never critical)")
    damage_repair_height: float | None = Field(description="Height of the damage from the ground with the blade pointing down (m)")
 
 
class TurbineRepairSummary(BaseModel):
    turbine_id: int = Field(description="Turbine ID")
    tower_height: float | None = Field(description="Tower height (m)")
    nb_damages: int = Field(description="Number of damages on this turbine")
    max_severity: int | None = Field(description="Highest damage severity (<= 2 is never critical)")
    avg_damage_repair_height: float | None = Field(description="Average repair height (m)")
    min_damage_repair_height: float | None = Field(description="Lowest damage (m)")
    max_damage_repair_height: float | None = Field(description="Highest damage (m)")
    recommended_equipment: str = Field(description="Smallest cherry picker reaching every damage of this turbine, or suspended platform")
    damages: list[DamageRepairHeight] = Field(description="Detail of every damage")
 
 
class CherryPickerCoverage(BaseModel):
    working_height: int = Field(description="Cherry picker working height (m)")
    reachable_damages: int = Field(description="Number of damages this cherry picker can reach")
    reachable_damages_pct: float = Field(description="Share of all damages it can reach (%)")
    reachable_critical_damages: int = Field(description="Number of reachable damages with severity > 2")
    fully_repairable_turbines: int = Field(description="Turbines where every damage can be reached")
 
 
class RepairHeightReport(BaseModel):
    nb_damages: int = Field(description="Total number of damages")
    detail_note: str | None = Field(default=None, description="Present when the damage detail was reduced because of its size")
    nb_critical_damages: int = Field(description="Damages with severity > 2")
    damages_needing_suspended_platform: int = Field(description="Damages too high for any cherry picker")
    cherry_picker_coverage: list[CherryPickerCoverage] = Field(description="What each cherry picker height can repair")
    turbines: list[TurbineRepairSummary] = Field(description="Detail per turbine")


# What's up

class WhatUpSeverity5Event(BaseModel):
    event_inspection: Inspection = Field(description='Inspection on which severity 5, or priority 1, have been reported')
    damage_ids: list [int] = Field(
        default_factory=list, description='Ids of the severity 5, or priority 1, damages found this inspection',
    )

class WhatUpWorseningTurbine(BaseModel):
    turbine: Turbine = Field(description='The inspection on which damage have worsen')
    this_year_damage_count: int = Field(description='Number of damage on the turbine this year')
    last_year_damage_count: int = Field(description='Number of damage on the turbine last year')

class WhatUpWorseningInspection(BaseModel):
    event_inspection: Inspection = Field(description='Inspection where the number of damage have increased')
    turbines: list[WhatUpWorseningTurbine] = Field(
        default_factory=list, description='Turbines on which the number of damage have increased',
    )

class WhatsUpResult(BaseModel):
    time_period: int = Field(description='Time period in days for which we looked at what is up')
    total_inspection: int = Field(description='Total number of inspection in this time peiod')
    severity_5_events: list[WhatUpSeverity5Event] = Field(
        default_factory=list, description='Inspections in this time period where there have been a severity 5, or priority 1',
    )
    worsening_events: list[WhatUpWorseningInspection] = Field(
        default_factory=list, description='Inspections in this time period where the number of damage have increased',
    )
    # first_site_inspections: list[Inspection] = Field(
    #     default_factory=list, description='Inspections in this time period on a site inspected for the first time',
    # )

# class ImportantEvent(BaseModel):
#     event_site: Site = Field(description='Site on which the event happened')
#     event_date: str = Field(description='The date at which the event has been reported (format YYYY-mm-dd)')
#     damage_id: int | None = Field(default=None, description='Precise the damage id when then event is a Severity 5')
    
# class WhatsNewGroupResult(BaseModel):
#     event_type: str = Field(description='Type of the important events listed in this group')
#     total_count: int = Field(description='Total of this type of event in this group result')
#     total_detailed: int = Field(description='Number of events detailed in the detailed_events list')
#     detailed_events: list[ImportantEvent] = Field(
#         default_factory=list, description='List of important events detailed in this group result',
#     )
    
# Get inspections

class MonthInspectionsResult(BaseModel):
    year: int = Field(description="Year when thiose inspections where done")
    month_number: int = Field(description="Month when those inspections where done in number format")
    total_count: int = Field(description='Number of inspections this month')
    total_detailed: int = Field(description='Number of inspections detailed in this result')
    month_end_detailed: Literal['beginning', 'end'] = Field(description='Which end of the month is detailed if there is too much inspections')
    detailed_inspections: list[Inspection] = Field(default_factory=list, description='List of inspections detailed in this result')
    
class RecentInspectionsResult(BaseModel):
    total_count: int = Field(description='Number of inspections ni the asked range')
    total_detailed: int = Field(description='Number of inspections detailed in this result')
    detailed_inspections: list[Inspection] = Field(default_factory=list, description='List of inspections detailed in this result')

# AEP loss

class BladeAEPLoss(BaseModel):
    blade: str = Field(description='The turbine blade this loss belongs to.')
    aep_loss_blade: float = Field(description='Annual Energy Production lost by this Blade, in MWh per year. Higher means the blade underperforms more.')
    aep_loss_blade_repair: float |None = Field(default=0,description='Annual Energy Production lost by this Blade, in MWh per year. Higher means the blade underperforms more.')
 
class TurbineAEPLoss(BaseModel):
    turbine: Turbine = Field(description='The turbine this loss belongs to, with its id and name.')
    aep_loss: float = Field(description='Annual Energy Production lost by this turbine, in MWh per year. Higher means the turbine underperforms more.')
    blade_aep_loss : list[BladeAEPLoss] |None = Field(default_factory=list,description='Per-blade breakdown of the turbine loss.')

class SiteAEPLoss(BaseModel):
    site: Site |None = Field(default=None,description='The site these figures cover.')
    inspection: Inspection | None  = Field(default=None,description='The inspection the figures were computed from. When the caller did not ask for a specific one, this is the most recent inspection available for the site.')
    aep_loss_site: float = Field(description='Total Annual Energy Production lost by the site, in MWh per year. Equals the sum of aep_loss over aep_loss_turbines.')
    aep_loss_turbines: list[TurbineAEPLoss] |None = Field(default_factory=list,description='Per-turbine breakdown of the site loss, one entry per turbine. Empty when the site has no AEP loss data.',)

# Repair Priorisation 

class SiteToRepair(BaseModel):
    site: Site = Field(description='The site these figures cover.')
    number_of_turbines: int = Field(description='The number of turbines on the site.')
    number_of_damages : int = Field(description='The Number of selected damages found over all turbines corresponding to the damage type')
    average_damages_per_turbine: float = Field(description='The average number of damages on each turbines.')
    aep_loss_site : SiteAEPLoss | None = Field(default=None,desciption='The total AEP Loss on this site')
    number_of_turbines_with_AEP: int | None   = Field(default=0,description="The Number of turbines with AEP loss computed")
    inspection : list[Inspection] = Field(description="The inspection linked to the site status")

# Damage erosion evolution 

class SiteEvolution(BaseModel):
    site: Site = Field(description='The site where the evolution of damages is covered')
    turbines : list[TurbineEvolution] = Field(description='The list of turbines with their damages and their evolutions')
    site_mean_growth_in_meters : float = Field(description='The mean growth of the erosion per turbine on this site')
    site_median_growth_in_meters : float = Field(description='The median growth of the erosion per turbine on this site')
    turbines_never_inspected : list[Turbine] |None = Field(default_factory=list,description='The turbines that where never inspected')
    turbines_without_previous : list[Turbine] |None = Field(default_factory=list,description='The turbine wthath doesn\'t have previous inspection')
    
class TurbineEvolution(BaseModel):
    turbine : Turbine = Field(description='The turbine where the damages evolved')
    inspection : Inspection = Field(description='The inspection linked to the damages evolution')
    previous_inspection : Inspection = Field(description='The previous inspection linked to the damages evolution')
    mean_growth : float = Field(description='The mean growth of the erosion on the turbine')
    blades : list[BladeEvolution]=Field(description='The breakdown of the blades damages evolution')

class BladeEvolution(BaseModel):
    blade_name : str = Field(description='The name of the blade')
    evolution_status :str=  Field(description='The status of the damages evolution')
    evolution_length : float | None = Field(default=None,description='The evolution length')
    total_eroded_length : float |None = Field(default=None,description="The total eroded lenght of the blade") 
    spread_to_pressure_side : bool | None  = Field(default=None,description='does the damages has spread to the pressure side')
    spread_to_suction_side : bool | None  = Field(default=None,description='does the damages has spread to the suction side')
    previously_spread_to_pressure_side : bool | None  = Field(default=None,description='does the damages was already spreading to the pressure side')
    previously_spread_to_suction_side : bool | None  = Field(default=None,description='does the damages was already spreading to the suction side')

# Crack details

class CrackDetails(BaseModel):
    damage_id: int = Field(description='Id of this crack in the turbinewatch database')
    face: str = Field(description='Blade face: Pressure side, Leading Edge, Suction side or Trailing Edge')
    radius: float | None = Field(default=None, description='Position along the blade, in meters from the root')
    length: float | None = Field(default=None,description='Crack length in meters. For multibranched and stripes cracks this is an area in square meters instead — see measured_as.')
    measured_as: str = Field( default='length', description='length (meters) or area (square meters)')
    orientation: str | None = Field(default=None, description='vertical, horizontal, or null when not recorded')
    shape: str | None = Field(default=None, description='straight, curved,  multibranched, or null')
    morpho:str | None = Field(default=None, description='If the crack is like hairline,dented,sawtooth or null.')
    stripes:str |None = Field(default=None,description='If the cracks is stripped or not stripped means lot of little cracks perpendicular in the same zone')
    severity: int | None = Field(default=None, description='Severity, 0 to 5')
    wind: str | None = Field(default=None, description='WIND index: the predicted risk of worsening')
    measure_source: str = Field(default='image',description='db when the length was measured by hand, image when computed from the photo. Do not compare two cracks measured differently.')
 
 
class BladeCrackDetails(BaseModel):
    blade: str = Field(description='Blade name: A, B or C')
    cracks_count: int = Field(description='How many cracks on this blade')
    max_severity: int | None = Field(default=None, description='Highest severity found on this blade')
    cracks: list[CrackDetails] = Field(default_factory=list, description='One entry per crack')
 
 
class TurbineCrackDetails(BaseModel):
    turbine: Turbine = Field(description='The turbine these cracks are on')
    cracks_count: int = Field(description='How many cracks on the whole turbine')
    blades: list[BladeCrackDetails] = Field(description='One entry per blade that has at least one crack. A blade with no crack does not appear.')


class CrackEvolutionDetails(BaseModel):
    damage_id: int = Field(description='Id of this crack at the current campaign. Several overlapping vertical cracks are reported as one, so this id identifies the group and is not stable across campaigns.')
    face: str = Field(description='Blade face: Pressure side, Leading Edge, Suction side or Trailing Edge')
    status: str = Field( description='new (absent at the previous campaign), grown, stable, or repaired (present before, gone now). Never shrunk: a crack cannot get smaller.')
    radius: float | None = Field(default=None, description='Position along the blade, in meters from the root')
    length: float | None = Field(default=None,description='Current crack length in meters. For multibranched and stripes cracks this is an area in square meters — see measured_as. Never add up the lengths of several cracks: two 30cm cracks are not one 60cm crack.')
    previous_length: float | None = Field( default=None, description='Same figure at the previous campaign')
    length_growth: float | None = Field(default=None,description='Meters this crack gained since the previous campaign. Null for a new or repaired crack, and for cracks measured as an area.')
    measured_as: str = Field(default='length', description='length (meters) or area (square meters)')
    severity: int | None = Field(default=None, description='Current severity, 0 to 5')
    previous_severity: int | None = Field(default=None, description='Severity at the previous campaign')
    orientation: str | None = Field(default=None, description='vertical, horizontal, or null when not recorded')
    shape: str | None = Field(default=None, description='straight, curved,  multibranched, or null')
    morpho:str | None = Field(default=None, description='If the crack is like hairline,dented,sawtooth or null.')
    stripes:str |None = Field(default=None,description='If the cracks is stripped or not stripped means lot of little cracks perpendicular in the same zone')
    wind: str | None = Field(default=None, description='WIND index: the predicted risk of worsening')
    growth_was_predicted: bool = Field(default=False,description='True when the WIND index had already forecast this growth')
 
class CrackCounts(BaseModel):
    total: int = Field(description='How many cracks in all')
    grown: int = Field(description='Cracks that grew beyond the measurement noise')
    new: int = Field(description='Cracks absent at the previous campaign')
    repaired: int = Field(description='Cracks present before and gone now')
    stable: int = Field(description='Cracks that did not change measurably')
 
 
class BladeCrackEvolution(BaseModel):
    blade_name: str = Field(description='Blade name: A, B or C')
    counts: CrackCounts = Field(description='How many cracks in each state')
    max_severity: int | None = Field( default=None, description='Highest severity found on this blade')
    longest_crack: float | None = Field(default=None,description='Length of the longest crack on this blade, in meters. Use this rather than any sum to say how bad the blade is.')
    cracks: list[CrackEvolutionDetails] = Field(default_factory=list, description='One entry per crack')
 
 
class TurbineCrackEvolution(BaseModel):
    turbine: Turbine = Field(description='The turbine these cracks are on')
    inspection: Inspection = Field(description='The campaign the figures come from')
    previous_inspection: Inspection = Field(description='The campaign compared against')
    counts: CrackCounts = Field(description='How many cracks in each state, all blades')
    max_severity: int | None = Field(default=None, description='Highest severity found on this turbine')
    longest_crack: float | None = Field(default=None, description='Length of the longest crack on this turbine, in meters')
    blades: list[BladeCrackEvolution] = Field(description='One entry per blade that has at least one crack. A blade with no crack does not appear.')
 
 
class SiteCrackEvolution(BaseModel):
    site: Site = Field(description='The site these figures cover')
    counts: CrackCounts = Field(description='How many cracks in each state across the whole site. Compare grown and new against total to say how the site is moving.')
    turbines_with_cracks: int = Field(description='How many turbines have at least one crack')
    turbines: list[TurbineCrackEvolution] = Field(description='One entry per turbine that has at least one crack, the ones with the most cracks first. A turbine with no crack does not appear: an absent turbine is a healthy one, not missing data.')
    turbines_never_inspected: list[Turbine] = Field(default_factory=list,description='Turbines of the park with no campaign at that date.')
    turbines_without_previous: list[Turbine] = Field(default_factory=list,description='Turbines with no earlier campaign to compare with.')

###Manufacturer

class ManufacturerDamages(BaseModel):
    manufacturer_name: str | None = Field(default='Unknown', description='Blade manufacturer (serial number prefix, e.g. EVC, IND, TPI)')
    turbine_models: str = Field(description='Turbine model labels matched by the search')
    damage_quantity: int = Field(description='Number of damages found on blades from this manufacturer')
    percentage_with_this_damage: float = Field(description='Share of all found damages belonging to this manufacturer (sums to 100%)')
    number_of_turbines_with_this_damage: int = Field(description='Number of distinct turbines with this damage on blades from this manufacturer')
    number_total_of_turbine: int = Field(description='Total number of turbines, within the matched models, with blades from this manufacturer (damaged or not)')
    percentage_of_turbines_with_this_damage_from_contructor: float = Field(description='Damage rate: damaged turbines / total turbines for this manufacturer. Use this to compare manufacturers')
     

#### Private functions for tools ####

def get_publish_date(planification_id) -> str:
    """
        Get the published date from cachebase, via l'API Laravel
    Args:
        planification_id (int): the planification_id to get the publish date

    """
    resp = requests.get(f"https://turbinewatch.singulair.io/api/cachebase/publish-date/{planification_id}")

    publish_date = resp.json().get("published_date")

    if publish_date: publish_date = str(publish_date).split(" ")[0]
    else : publish_date = "No Publish Date"
    return publish_date
    
def get_turbine_age(tid):
    """
        Get the turbine age from TW
    Args:
        tid (int): The turbine id
    """     
    TW_DB_CURSOR.execute('SELECT EXTRACT(YEAR FROM AGE(NOW(), entry_service))::INT AS turbine_age FROM turbines WHERE turbines.id =%s',(tid,))
    age = TW_DB_CURSOR.fetchone()[0]
    age = int(age) if age is not None else None
    if age is None: return None
    if age > 30 : return None
    return age

def get_site_name(site_id):
    """
        Get the site name from TW
    Args:
        site_id (int): the site_id to get the site name
    """
    TW_DB_CURSOR.execute("SELECT sites.name FROM sites WHERE sites.id = %s", (site_id, ))
    site_name = TW_DB_CURSOR.fetchone()
    return str(site_name)
