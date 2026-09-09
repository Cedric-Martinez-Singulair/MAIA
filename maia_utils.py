from __future__ import annotations
from contextlib import contextmanager

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from sshtunnel import SSHTunnelForwarder

from pydantic import BaseModel, Field
from typing import Literal

### TW access ###

tw_db_tunnel = SSHTunnelForwarder(
    ('192.168.154.218', 22),
    ssh_username="cmartinez",
    ssh_password="cmartinez",
    remote_bind_address=("localhost", 5432),
)
tw_db_tunnel.start()

TW_DB_PARAMS = dict(
    dbname="windwatch",
    user="windwatch",
    password="windwatch",
    host="localhost",
    port=tw_db_tunnel.local_bind_port,
)

TW_DB_POOL = ThreadedConnectionPool(minconn=2, maxconn=200, **TW_DB_PARAMS)

@contextmanager
def tw_cursor():
    conn = TW_DB_POOL.getconn()
    try:
        conn.autocommit = True # lectures seules : évite les transactions ouvertes
        with conn.cursor() as cur:
            yield cur
    finally:
        TW_DB_POOL.putconn(conn)
        
# Compatibilité : tes tools existants continuent de fonctionner tels quels.
tw_db_connection = psycopg2.connect(**TW_DB_PARAMS)
TW_DB_CURSOR = tw_db_connection.cursor()

#### CACHE BASE ######

CB_DB_PARAMS = dict(
    dbname="cachebase",
    user="windwatch",
    password="windwatch",
    host="localhost",
    port=tw_db_tunnel.local_bind_port,
)
cb_db_connection = psycopg2.connect(**CB_DB_PARAMS)
CB_DB_CURSOR = cb_db_connection.cursor()


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
    
class Site(BaseModel):
    id: int = Field(description='Site id, needed to identify the site in the turbinewatch database')
    name: str = Field(description='Site name')
    turbines: list[Turbine] | None = Field(
        default_factory=list, description='Turbines belonging to this site',
    )
    
class Country(BaseModel):
    id: int = Field(description='Country id, needed to identify the country in the turbinewatch database')
    name: str = Field(description='Country name')
    sites: list[Turbine] | None  = Field(
        default_factory=list, description='Sites located in this country',
    )

class Inspection(BaseModel):
    id: int = Field(description='Inspection id, needed to identify the inspection in the turbinewatch database')
    date: str | None = Field(default=None, description='Inspection date (format YYYY-mm-dd)')
    published_date: str = Field(description='Inspection publication date (format YYYY-mm-dd)')
    site: Site | None = Field(default=None, description='Inspected site.')
    
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
        default_factory=list, description='Damage count on each turbine associated to the inspection',
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
        default_factory=list, description='List of damage ids',
    )

class TurbineDamageListResult(BaseModel):
    turbine: Turbine = Field(description='Turbine on which the damage ids are listed')
    damage_list_filter: str = Field(description='Precise if the turbine damages are filtered before being listed')
    damage_id_lists: list[DamageListResult] = Field(
        default_factory=list, description='Lists of damage ids',
    )

class InspectionDamageListResult(BaseModel):
    inspection: Inspection = Field(description='Inspection at which the damage ids are listed')
    turbine_damage_id_lists: list[TurbineDamageListResult] = Field(
        default_factory=list, description='Damage ids list on each turbine associated to the inspection',
    )

# Repair cost

class DamageGroupRepairCost(BaseModel):
    severity: int = Field(description='Severity of the group of damage of which the repair cost is estimated')
    damage_type: DamageType = Field(description='Damage type of the group of damage of which the repair cost is estimated')
    damage_count: int = Field(description='Count of damage in the group of which the repair cost is estimated')
    repair_cost: float = Field(description='Repair cost for this group of damage in USD')
    explanation: str = Field(description='Precise how the repair cost have been calculated')

class TurbineRepairCost(BaseModel):
    turbine: Turbine = Field(description='Turbine on which are the damages for which we estimated the repair cost')
    damage_group_repair_cost: list[DamageGroupRepairCost] = Field(
        default_factory=list, description='List of group of damage for which we estimated the repair cost',
    )

class InspectionRepairCost(BaseModel):
    inspection: Inspection = Field(description='Inspection at which we estimated the repair costs')
    turbine_repair_costs: list[TurbineRepairCost] = Field(
        default_factory=list, description='List of turbines on which there are groups of damages for which we estimated the repair cost',
    )

# What's new

class ImportantEvent(BaseModel):
    event_site: Site = Field(description='Site on which the event happened')
    event_date: str = Field(description='The date at which the event has been reported (format YYYY-mm-dd)')
    damage_id: int | None = Field(default=None, description='Precise the damage id when then event is a Severity 5')
    
class WhatsNewGroupResult(BaseModel):
    event_type: str = Field(description='Type of the important events listed in this group')
    total_count: int = Field(description='Total of this type of event in this group result')
    total_detailed: int = Field(description='Number of events detailed in the detailed_events list')
    detailed_events: list[ImportantEvent] = Field(
        default_factory=list, description='List of important events detailed in this group result',
    )
    
# Get inspections

class MonthInspectionsResult(BaseModel):
    year: int = Field(description="Year when thiose inspections where done")
    month_number: int = Field(description="Month when those inspections where done in number format")
    total_count: int = Field(description='Number of inspections this month')
    total_detailed: int = Field(description='Number of inspections detailed in this result')
    month_end_detailed: Literal['beginning', 'end'] = Field(description='Which end of the month is detailed if there is too much inspections')
    detailed_inspections: list[Inspection] = Field(
        default_factory=list, description='List of inspections detailed in this result',
    )
    
class RecentInspectionsResult(BaseModel):
    total_count: int = Field(description='Number of inspections ni the asked range')
    total_detailed: int = Field(description='Number of inspections detailed in this result')
    detailed_inspections: list[Inspection] = Field(
        default_factory=list, description='List of inspections detailed in this result',
    )

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
    inspection: Inspection | None  = Field(default=None,
        description='The inspection the figures were computed from. When the caller did not ask for a specific one, this is the most recent inspection available for the site.'
    )
    aep_loss_site: float = Field(
        description='Total Annual Energy Production lost by the site, in MWh per year. Equals the sum of aep_loss over aep_loss_turbines.')
    aep_loss_turbines: list[TurbineAEPLoss] |None = Field(
        default_factory=list,
        description='Per-turbine breakdown of the site loss, one entry per turbine. Empty when the site has no AEP loss data.',)

# Repair Priorisation 

class SiteStatus(BaseModel):
    site: Site = Field(description='The site these figures cover.')
    number_of_turbines: int = Field(description='The number of turbines on the site.')
    number_of_damages : int = Field(description='The Number of selected damages found over all turbines corresponding to the damage type')
    average_damages_per_turbine: float = Field(description='The average number of damages on each turbines.')
    aep_loss_site : SiteAEPLoss|None = Field(default=None,desciption='The total AEP Loss on this site')
    number_of_turbines_with_AEP:int = Field(description="The Number of turbines with AEP loss computed")
    inspection : list[Inspection] = Field(description="The inspection linked to the site status")



#### Private functions for tools ####

def get_publish_date(planification_id) -> str:
    """
        Get the published date from cachebase
    Args:
        planification_id (int): the planification_id to get the publish date
    """
    # CB_DB_CURSOR.execute("SELECT published_date FROM prepared_reports_by_campaign WHERE pid = %s", (planification_id, ))
    # publish_date = CB_DB_CURSOR.fetchone()
    # publish_date = str(publish_date).split(" ")[0]
    # return publish_date
    
    #TODO attendre que ça fasse pas planter TurbineWatch
    return "Published date unknown"
    
def get_site_name(site_id):
    """
        Get the site name from cachebase
    Args:
        site_id (int): the site_id to get the site name
    """
    # CB_DB_CURSOR.execute("SELECT DISTINCT snam FROM prepared_reports_by_campaign WHERE sid = %s", (site_id, ))
    # site_name = CB_DB_CURSOR.fetchone()
    # return str(site_name)
    
    #TODO attendre que ça fasse pas planter TurbineWatch
    return "Site name unknown"