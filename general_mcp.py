from langchain_core.tools import tool
from maia_utils import *

@tool
def get_wind_index_explanation(specific_wind_index: str | None = None) -> str:
    '''Explain what the WIND index is and how to read it.
    
    Call this before interpreting or commenting on any wind index value, so that
    the explanation given to the user matches the definition used in turbinewatch.
    
    Args:
        wind_index: Name of one specific index, to get its own description.
            If omitted, returns a general explanation of the WIND index.
            
    Returns:
        A plain-text explanation, meant to be relayed to the user.
    '''
    print("TOOL_CALL get_wind_index_explanation", specific_wind_index)
    
    if specific_wind_index is None:
        return """The WIND index indicates the likeliness for a damage to worsen.
        It works especially well on Leading Edge Erosion damage.
        
        The wind is composed of 2 parts.
        The first part of the wind is a letter (C, M or S) which describe the current state of the damage.
        C means the damage is only cosmetic.
        M means the damage is medium so, it already affect to the turbine efficiency.
        S means the damage is severe. Severe can mean the turbine efficiency is severly affected or the damage is a threat for the turbine integrity.
        The second part of the wind is a number (1, 2 or 3) which describe the likeliness for the damage to worsen.
        1 means this type of damage usually don't worsen.
        2 means this damage is likely to worsen.
        3 means either that the damage is very likely to worsen either that it is likely to worsen dangerously.
        
        A WIND value of F is used to says that there is already a blade failure."""
    else:
        specific_wind_index = specific_wind_index.upper()
        
        if specific_wind_index == 'C1':
            return "This damage is cosmetic and won't worsen."
        elif specific_wind_index == 'C2':
            return "This damage will likely worsen but remaining cosmetic."
        elif specific_wind_index == 'C3':
            return "This damage is cosmetic but could worsen and become medium."
        elif specific_wind_index == 'M1':
            return "This damage affect blade efficiency but won't worsen."
        elif specific_wind_index == 'M2':
            return "This damage will worsen, which is bad for blade efficiency."
        elif specific_wind_index == 'M3':
            return "This damage is not severe yet but could worsen in this way."
        elif specific_wind_index == 'S1':
            return "This type of damage is dangerous but likely don't worsen."
        elif specific_wind_index == 'S2':
            return "This damage is dangerous and will likely worsen."
        elif specific_wind_index == 'S3':
            return "A blade failure could happen if this damage is not repaired."
        elif specific_wind_index == 'F':
            return "The blade is broken (Failure)."
        else:
            return "specific_wind_index not valid"
        
@tool
def what_is_new(look_back_days: int = 10) -> str:
    '''Summarize the important events detected across the monitored wind farms over a recent period.
    
    Use this for open-ended status questions: "what's up", "quoi de neuf", "what is important to know", "any alerts?", "brief me on the fleet".
    
    Event types:
    - FIR (Failed Inspection Report): an inspection could not be carried out because of an issue.
    - Rework: a turbine must be inspected again because of image quality problems.
    - Severity 5: an inspection revealed a severity 5 damage, the most critical level.
    
    Args:
        look_back_days: size of the look-back window, in days (default 10). Must correspond precisely to the number of day needed to cover the recent period whose we want informations.
    
    Returns:
        A list of WhatsNewGroupResult objects, one for each type of important event that happened in the asked time period.'''
    print("TOOL_CALL what_is_new", look_back_days)
    
    req_str = " \
        SELECT DISTINCT sites.id, sites.name, CAST(_inspected.date AS VARCHAR), fir_causes.label_en, _incident_records.id \
        FROM (SELECT * FROM inspections_status WHERE status_id = 4 AND date > (NOW() - INTERVAL %s DAY) ) _inspected \
        JOIN turbines ON turbines.id = _inspected.turbine_id \
        JOIN sites ON sites.id = turbines.site_id \
        LEFT JOIN (SELECT * FROM fir_records WHERE deleted_at IS NULL) _firs \
        ON _firs.planification_id = _inspected.planification_id AND _firs.turbine_id = _inspected.turbine_id \
        LEFT JOIN fir_causes ON fir_causes.id = _firs.cause_id \
        LEFT JOIN (SELECT * FROM incident_records WHERE deleted_at IS NULL AND criticality_id = 5) _incident_records \
        ON _incident_records.planification_id = _inspected.planification_id AND _incident_records.turbine_id = _inspected.turbine_id \
        WHERE _firs.cause_id IS NOT NULL OR _incident_records.criticality_id IS NOT NULL \
        ORDER BY CAST(_inspected.date AS VARCHAR) DESC \
    "
    
    print("--- REQ", req_str, look_back_days)
    TW_DB_CURSOR.execute(req_str, (str(look_back_days),))
    
    group_result_dict = {}
    for row in TW_DB_CURSOR:
        site_id, site_name, date, cause, damage_id = row
        
        if damage_id is not None:
            event_type = 'Severity 5'
        else:
            if cause == 'Quality':
                event_type = 'Rework'
            else:
                event_type = 'FIR'
        
        if event_type not in group_result_dict:
            group_result_dict[event_type] = WhatsNewGroupResult(
                event_type=event_type,
                total_count=0,
                total_detailed=0,
                detailed_events=[]
            )
        if group_result_dict[event_type].total_detailed < 20:
            group_result_dict[event_type].detailed_events.append(ImportantEvent(
                event_site=Site(id=site_id, name=site_name),
                event_date=date.split(" ")[0],
                damage_id=damage_id
            ))
            group_result_dict[event_type].total_detailed += 1
        group_result_dict[event_type].total_count += 1
    
    group_result = []
    for event_type in group_result_dict:
        group_result.append(group_result_dict[event_type])
    
    print("--- RES", group_result)
    return str(group_result)
