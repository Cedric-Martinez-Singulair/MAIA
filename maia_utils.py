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
    age: int | None = Field(default=None, description='Turbine age, since entry service, at a specific planification')
    model: TurbineModel | None = Field(default=None, description='Turbine model')
    damages: list[Damage] | None = Field(default_factory=list, description='all the damages located on this turbine')
    
class Site(BaseModel):
    id: int = Field(description='Site id, needed to identify the site in the turbinewatch database')
    name: str = Field(description='Site name')
    turbines: list[Turbine] | None  = Field(
        default_factory=list, description='Turbines belonging to this site',
    )
    
class Country(BaseModel):
    id: int = Field(description='Country id, needed to identify the country in the turbinewatch database')
    name: str = Field(description='Country name')
    sites: list[Turbine] | None  = Field(
        default_factory=list, description='Sites located in this country',
    )

class Planification(BaseModel):
    id: int = Field(description='Planification id, needed to identify the planification in the turbinewatch database')
    date: str = Field(description='Planification date (format YYYY-mm-dd)')
    
class Damage(BaseModel):
    id: int = Field(description='Id of this specific damage in the turbinewatch database')
    damaged_part: BladeComponent | None = Field(default=None, description='Component of the blade where this damage is located')
    damage_type: DamageType | None = Field(default=None, description='Type of this specific damage')
    site: Site | None = Field(default=None, description='Site where the damage is located')
    turbine: Turbine | None = Field(default=None, description='Turbine where the damage is located')
    blade: str | None = Field(default=None, description='Blade where the damage is located')
    face: str | None = Field(default=None, description='Face where the damage is located')
    radius: int | None = Field(default=None, description='Radius where the damage is located')
    planification: Planification | None = Field(default=None, description='Planification at which the damage have been reported')
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

class PlanificationDamageCountResult(BaseModel):
    planification: Planification = Field(description='Planification at which damage on turbines are counted')
    turbine_damage_counts: list[TurbineDamageCountResult] = Field(
        default_factory=list, description='Damage count on each turbine associated to the planification',
    )

# List damages

class DamageListResult(BaseModel):
    blade: str | None = Field(default=None, description='Blade where the damages are located')
    face: str | None = Field(default=None, description='Face where the damages are located')
    blade_component: BladeComponent | None = Field(default=None, description='Blade component where the damages are located')
    damage_type: DamageType | None = Field(default=None, description='Type of the damages')
    severity: int | None = Field(default=None, description='Severity of the damages')
    damage_ids: list[int] = Field(
        default_factory=list, description='List of damage ids',
    )

class TurbineDamageListResult(BaseModel):
    turbine: Turbine = Field(description='Turbine on which the damage ids are listed')
    damage_list_filter: str = Field(description='Precise if the turbine damages are filtered before being listed')
    damage_id_lists: list[DamageCountResult] = Field(
        default_factory=list, description='Lists of damage ids',
    )

class PlanificationDamageListResult(BaseModel):
    planification: Planification = Field(description='Planification at which the damage ids are listed')
    turbine_damage_id_lists: list[TurbineDamageCountResult] = Field(
        default_factory=list, description='Damage ids list on each turbine associated to the planification',
    )

# Repair cost

class DamageGroupRepairCost(BaseModel):
    severity: int = Field(description='Severity of the group of damage of which the repair cost is estimated')
    damage_type_id: int = Field(description='Damage type id of the group of damage of which the repair cost is estimated')
    damage_count: int = Field(description='Count of damage in the group of which the repair cost is estimated')
    repair_cost: float = Field(description='Repair cost for this group of damage in USD')
    explanation: str = Field(description='Precise how the repair cost have been calculated')

class TurbineRepairCost(BaseModel):
    turbine: Turbine = Field(description='Turbine on which are the damages for which we estimated the repair cost')
    damage_group_repair_cost: list[DamageGroupRepairCost] = Field(
        default_factory=list, description='List of group of damage for which we estimated the repair cost',
    )

class PlanificationRepairCost(BaseModel):
    planification: Planification = Field(description='Planification at which we estimated the repair costs')
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

# AEP loss

class BladeAEPLoss(BaseModel):
    blade: str = Field(description='The turbine blade this loss belongs to.')
    aep_loss_blade: float = Field(description='Annual Energy Production lost by this Blade, in MWh per year. Higher means the blade underperforms more.')
    aep_loss_blade_repair: float |None = Field(default=0,description='Annual Energy Production lost by this Blade, in MWh per year. Higher means the blade underperforms more.')
 
class TurbineAEPLoss(BaseModel):
    turbine: Turbine = Field(description='The turbine this loss belongs to, with its id and name.')
    aep_loss: float = Field(description='Annual Energy Production lost by this turbine, in MWh per year. Higher means the turbine underperforms more.')
    blade_aep_loss : list[BladeAEPLoss] |None = Field(default_factory=list,description='Per-blade breakdown of the turbine loss.')


## Thinking about how i can implement "the calcul of the AEP LOSS if i potentially repair a blade or some of the damages" 
# class TurbineAEPLossRepaired(BaseModel):
#     turbine: Turbine = Field(description='The turbine this loss belongs to, with its id and name.')
#     aep_loss: float = Field(description='Annual Energy Production lost by this turbine, in MWh per year. Higher means the turbine underperforms more.')


class SiteAEPLoss(BaseModel):
    site: Site = Field(description='The site these figures cover.')
    planification: Planification = Field(
        description='The planification the figures were computed from. When the caller did not ask for a specific one, this is the most recent planification available for the site.'
    )
    aep_loss_site: float = Field(
        description='Total Annual Energy Production lost by the site, in MWh per year. Equals the sum of aep_loss over aep_loss_turbines.')
    aep_loss_turbines: list[TurbineAEPLoss] = Field(
        default_factory=list,
        description='Per-turbine breakdown of the site loss, one entry per turbine. Empty when the site has no AEP loss data.',)
    MWh_price: float | None = Field(default=108.00, description='The price of 1 MWh on the electricity Wholesale Market')
        
        
