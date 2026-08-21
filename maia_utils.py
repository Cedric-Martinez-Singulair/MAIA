import psycopg2
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

tw_db_connection = psycopg2.connect(
    dbname="windwatch",
    user="windwatch",
    password="windwatch",
    host="localhost",
    port=tw_db_tunnel.local_bind_port,
)
TW_DB_CURSOR = tw_db_connection.cursor()

### Entity BaseModel classes ###

class BladePart(BaseModel):
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

class Site(BaseModel):
    id: int = Field(description='Site id, needed to identify the site in the turbinewatch database')
    name: str = Field(description='Site name')
    turbines: list[Turbine] | None  = Field(
        default_factory=list, description='Turbines belonging to this site',
    )

class Planification(BaseModel):
    id: int = Field(description='Planification id, needed to identify the planification in the turbinewatch database')
    date: str = Field(description='Planification date (format YYYY-mm-dd)')
    
class Damage(BaseModel):
    id: int = Field(description='Id of this specific damage in the turbinewatch database')
    damaged_part: BladePart | None = Field(default=None, description='Blade part where this damage is')
    damage_type: DamageType | None = Field(default=None, description='Type of this specific damage')
    site: Site | None = Field(default=None, description='Site where the damage is located')
    turbine: Turbine | None = Field(default=None, description='Turbine where the damage is located')
    blade: str | None = Field(default=None, description='Blade where the damage is located')
    face: str | None = Field(default=None, description='Face where the damage is located')
    radius: int | None = Field(default=None, description='Radius where the damage is located')
    planification: Planification | None = Field(default=None, description='Planification at which the damage have been reported')

### Result BaseModel classes ###

class TurbineDamageCountResult(BaseModel):
    turbine: Turbine = Field(description='Turbine on which the damage count is')
    damage_count_filter: str = Field(description='Precise if the turbine damages are filtered before being count')
    damage_count: int = Field(description='Count of damage, corresponding to the damage_count_filter, on this turbine')

class PlanificationDamageCountResult(BaseModel):
    planification: Planification = Field(description='Planification at which damage on turbines are counted')
    turbine_damage_counts: list[TurbineDamageCountResult] = Field(
        default_factory=list, description='Damage count on each turbine associated to the planification',
    )

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
    