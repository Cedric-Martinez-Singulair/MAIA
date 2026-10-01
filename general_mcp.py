import json

from pydantic import TypeAdapter
from langchain_core.tools import tool
from maia_utils import *
from damage_utils import *

@tool
def update_persona(persona_str: Literal["blade_analyst", "service_manager", "capacity_planner"]) -> str:
    """Update to global persona value which define if the current user is a
    blade analyst, service manager or capacity planner.
    
    Use this tool if the user precise that he is a blade_analyst, service_manager or capacity_planner.
    
    It will take effect with the next question.
    """
    print("TOOL_CALL update_persona", persona_str)
    
    GLOBAL_INFOS['CURRENT_PERSONA'] = persona_str
    
    resp = "Persona updated to " + persona_str + ". It will take effect with the next question."; print(resp)
    return resp

# @tool
# def get_wind_index_explanation(specific_wind_index: str | None = None) -> str:
#     """Explain what the WIND index is and how to read it.

#     Call this before interpreting or commenting on any wind index value, so that
#     the explanation given to the user matches the definition used in turbinewatch.

#     Args:
#         wind_index: Name of one specific index, to get its own description.
#         If omitted, returns a general explanation of the WIND index.

#     Returns:
#         A plain-text explanation, meant to be relayed to the user.
#     """
#     print("TOOL_CALL get_wind_index_explanation", specific_wind_index)

#     #TODO TypeAdapter avant de return
#     if specific_wind_index is None:
#         return """The WIND index indicates the likeliness for a damage to worsen.
#         It works especially well on Leading Edge Erosion damage.
        
#         The wind is composed of 2 parts.
#         The first part of the wind is a letter (C, M or S) which describe the current state of the damage.
#         C means the damage is only cosmetic.
#         M means the damage is medium so, it already affect to the turbine efficiency.
#         S means the damage is severe. Severe can mean the turbine efficiency is severly affected or the damage is a threat for the turbine integrity.
#         The second part of the wind is a number (1, 2 or 3) which describe the likeliness for the damage to worsen.
#         1 means this type of damage usually don't worsen.
#         2 means this damage is likely to worsen.
#         3 means either that the damage is very likely to worsen either that it is likely to worsen dangerously.
        
#         A WIND value of F is used to says that there is already a blade failure."""
#     else:
#         specific_wind_index = specific_wind_index.upper()

#         if specific_wind_index == "C1":
#             return "This damage is cosmetic and won't worsen."
#         elif specific_wind_index == "C2":
#             return "This damage will likely worsen but remaining cosmetic."
#         elif specific_wind_index == "C3":
#             return "This damage is cosmetic but could worsen and become medium."
#         elif specific_wind_index == "M1":
#             return "This damage affect blade efficiency but won't worsen."
#         elif specific_wind_index == "M2":
#             return "This damage will worsen, which is bad for blade efficiency."
#         elif specific_wind_index == "M3":
#             return "This damage is not severe yet but could worsen in this way."
#         elif specific_wind_index == "S1":
#             return "This type of damage is dangerous but likely don't worsen."
#         elif specific_wind_index == "S2":
#             return "This damage is dangerous and will likely worsen."
#         elif specific_wind_index == "S3":
#             return "A blade failure could happen if this damage is not repaired."
#         elif specific_wind_index == "F":
#             return "The blade is broken (Failure)."
#         else:
#             return "specific_wind_index not valid"

@tool
def get_campaign_damage_infos(
campaign_year: int, 
damage_type_id: int | None = None, 
country_id: int | None = None, 
client_id: int | None = None, 
severities: list[int] | None = None,
avg_type: Literal["damage_type", "country", "client"] | None = None
) -> str:
    """Give the number of damage recorded during a specific campaign year.
    
    Args:
        campaign_year: Year of the inspection campaign, e.g. 2026.
        damage_type_id: Damage type id to filter on. Never guess this value.
        country_id: Country to filter on. Use get_country_ids_by_name to resolve a
            country name into its id. Never guess this value.
        client_id: Client Company id to filter on. Never guess this value.
        severities: Restrict to some severities.
        avg_type: Grouping key for the average. Combinable with any of the filters above.
    Returns:
        If avg_type is None: The count of damage for this inspection campaign. 
        Otherwise: a dict mapping each group (damage type, country, or client)
        to the number of damage in that group for this inspection campaign.
    """
    print("TOOL_CALL get_campaign_damage_infos", campaign_year, damage_type_id, country_id, client_id, severities, avg_type)
    
    if avg_type is None:
        req_str = "SELECT COUNT(*) "
    elif avg_type == 'damage_type':
        req_str = "SELECT defect_types.label_en, COUNT(*) "
    elif avg_type == 'country':
        req_str = "SELECT countries.label_en, COUNT(*) "
    elif avg_type == 'client':
        req_str = "SELECT societes.label_en, COUNT(*) "
    
    req_str += " \
        FROM incident_records \
        JOIN planifications ON planifications.id = incident_records.planification_id \
        JOIN schedule_inspections ON schedule_inspections.planification_id = planifications.id \
        JOIN defect_types ON defect_types.id = incident_records.defect_type_id \
        JOIN turbines ON turbines.id = incident_records.turbine_id \
        JOIN sites ON sites.id = turbines.site_id \
        JOIN countries ON countries.id = sites.country_id \
        JOIN societes ON societes.id = planifications.societe_id \
        WHERE planifications.deleted_at IS NULL AND incident_records.deleted_at IS NULL \
        AND schedule_inspections.campaign = %s \
    "
    req_params = [campaign_year]
    
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] == 15 or client_id == 15: 
        req_str += "AND incident_records.priority_id <> 5 "
    
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] != 5:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(GLOBAL_INFOS['CURRENT_COMPANY_ID'])
    elif client_id is not None:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(client_id)
    
    if damage_type_id is not None:
        req_str += "AND defect_types.id = %s "; req_params.append(damage_type_id)
    if country_id is not None:
        req_str += "AND sites.country_id = %s "; req_params.append(country_id)
    
    if severities is not None and len(severities) > 0:
        req_str += "AND incident_records.criticality_id IN ("
        for severity in severities:
            severity_id = severity if severity != 0 else 6
            req_str += "%s, "; req_params.append(severity_id)
        req_str = req_str[:-2] + ") " # Remove last comma
    
    if avg_type == 'damage_type':
        req_str += "GROUP BY defect_types.label_en "
    elif avg_type == 'country':
        req_str += "GROUP BY countries.label_en "
    elif avg_type == 'client':
        req_str += "GROUP BY societes.label_en "
    
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] == 15 or client_id == 15: 
        req_str = req_str.replace("criticality_id", "priority_id")
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    if avg_type is None:
        res_damage_count = None
    else:
        res_damage_count = {}
    
    for row in TW_DB_CURSOR:
        if avg_type is None:
            res_damage_count = row[0]
        else:
            avg_key, damage_count = row
            if avg_key is not None: 
                res_damage_count[avg_key] = damage_count
    
    if isinstance(res_damage_count, int):
        resp = TypeAdapter(int).dump_json(res_damage_count).decode()
    else:
        resp = TypeAdapter(dict[str, int]).dump_json(res_damage_count).decode()
    
    print("--- RES ", resp)
    return resp

@tool
def filter_report(
campaign_year: int | None = None,
country_id: int | None = None,
inspection_type_id: int | None = None,
client_id: int | None = None
) -> str:
    """Apply filters to the list of reports currently displayed to the user.
    
    Use this when the user asks to see, show or narrow down reports by campaign year,
    country or inspection type.
    Do NOT use it to answer questions about report content — it only changes what the
    frontend displays.
    
    You MUST copy the returned block into your answer exactly as it is, unchanged,
    on its own lines. Add one short sentence before it saying which filters you applied.
    At least one argument is required.
    
    Args:
        campaign_year: Year of the inspection campaign, e.g. 2026.
        country_id: Country to filter on. Use get_country_ids_by_name to resolve a
            country name into its id. Never guess this value.
        inspection_type_id: Inspection type to filter on. Never guess this value.
        client_id: Client Company id to filter on (Singulair user only). Never guess this value.
    Returns:
        A fenced block tagged "filter" containing the filters as JSON, to be copied
        verbatim into your answer.
    """
    print("TOOL_CALL filter_report", campaign_year, country_id, inspection_type_id, client_id)
    
    country_code = None
    if country_id is not None:
        TW_DB_CURSOR.execute("SELECT code FROM countries WHERE id = %s", (country_id, ))
        row = TW_DB_CURSOR.fetchone()
        country_code = row[0]
    
    filters = {
        "campaign": campaign_year,
        "country_code": country_code,
        "control_type": inspection_type_id,
        "societe_id": client_id
    }
    filters = {k: v for k, v in filters.items() if v is not None}

    if not filters:
        resp = "No filter was provided. Ask the user which campaign, country or inspection type they want."; print("--- RES", resp)
        return resp

    result = "```filter\n" + json.dumps(filters, indent=2) + "\n```"

    resp = TypeAdapter(str).dump_json(result).decode(); print("--- RES", resp)
    return resp

@tool
def what_s_up(
look_back_days: int = 10, 
country_id: int | None = None, 
client_id: int | None = None
) -> str:
    """Summarize the important events detected across the monitored wind farms over a recent period.
    
    Use this tool if the user ask what is up or what he needs to know abount recent inspections.
    
    Args:
        look_back_days: Size of the look-back window, in days (default 10). 
            Must correspond precisely to the number of day needed to cover the recent period whose we want informations.
        country_id: Restrict to the inspections on a specific country
        client_id: Client Company id to filter on (Singulair user only). Never guess this value.
    Returns:
        A WhatsUpResult object with all the details of what happened recently.
    """
    print("TOOL_CALL what_s_up", look_back_days, country_id, client_id)
    
    ### TOTAL INSP ###
    
    nb_insp_req_str = " \
        SELECT COUNT(DISTINCT _inspected.id) FROM (SELECT * FROM inspections_status WHERE status_id = 4 AND date > (NOW() - INTERVAL %s DAY) ) _inspected \
        JOIN planifications ON planifications.id = _inspected.planification_id \
        JOIN turbines ON turbines.id = _inspected.turbine_id \
        JOIN sites ON sites.id = turbines.site_id \
    "
    nb_insp_req_params = [str(look_back_days)]

    where_found = False
    
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] != 5:
        nb_insp_req_str += "WHERE planifications.societe_id = %s "; where_found = True
        nb_insp_req_params.append(GLOBAL_INFOS['CURRENT_COMPANY_ID'])
    elif client_id is not None:
        nb_insp_req_str += "WHERE planifications.societe_id = %s "; where_found = True
        nb_insp_req_params.append(client_id)

    if country_id is not None:
        if not where_found:
            nb_insp_req_str += "WHERE sites.country_id = %s "
        else:
            nb_insp_req_str += "AND sites.country_id = %s "
        
        nb_insp_req_params.append(country_id)
        
    print("--- REQ", nb_insp_req_str, nb_insp_req_params)
    TW_DB_CURSOR.execute(nb_insp_req_str, nb_insp_req_params)
    
    row = TW_DB_CURSOR.fetchone()
    total_insp = row[0]
    
    ### SEVERITY 5 ###
    
    severity_5_raw_data = get_recent_severity_5(look_back_days, country_id, client_id)
    
    result = WhatsUpResult(
        time_period=look_back_days,
        total_inspection=total_insp,
        severity_5_events=[],
        worsening_events=[]
    )
    
    found_planif_id = []
    for record in severity_5_raw_data:
        if record['planif_id'] not in found_planif_id:
            result.severity_5_events.append(WhatUpSeverity5Event(
                event_inspection=Inspection(
                    id=record['planif_id'], date=record['planif_date'],
                    site=Site(id=record['site_id'], name=record['site_name'])
                )
            ))
            found_planif_id.append(record['planif_id'])
            
        result.severity_5_events[-1].damage_ids.append(record['damage_id'])
        
    ### DAMAGE EVOLUTION ###
    
    damage_evolution_raw_data = get_recent_damage_evolution(look_back_days, country_id, client_id)
    found_inspection_ids = []
    for record in damage_evolution_raw_data:
        if record['count_2026'] > 80 and record['count_2026'] > 2 * record['count_2025']:
            if record['planification_id'] not in found_inspection_ids:
                result.worsening_events.append(WhatUpWorseningInspection(
                    event_inspection=Inspection(
                        id=record['planification_id'], date=record['date'].split(" ")[0],
                        site=Site(id=record['site_id'], name=record['site'])
                    ),
                    turbines=[]
                ))
                found_inspection_ids.append(record['planification_id'])
        
            result.worsening_events[-1].turbines.append(WhatUpWorseningTurbine(
                turbine=Turbine(id=record['turbine_id'], name=record['turbine']),
                this_year_damage_count=record['count_2026'], #TODO modifier pour que ça marche l'année prochaine
                last_year_damage_count=record['count_2025']
            ))
    
    #######
    
    resp = TypeAdapter(WhatsUpResult).dump_json(result).decode(); print("--- RES ", resp)
    return resp

# @tool
# def singulair_what_s_up(look_back_days: int = 10) -> str:
#     """Summarize the important events detected across the monitored wind farms over a recent period.

#     Use this for open-ended status questions: "what's up", "what is important to know", "any alerts?", "brief me on the fleet".

#     Event types:
#     - FIR (Failed Inspection Report): an inspection could not be carried out because of an issue.
#     - Rework: a turbine must be inspected again because of image quality problems.
#     - Severity 5: an inspection revealed a severity 5 damage, the most critical level.

#     Args:
#         look_back_days: Size of the look-back window, in days (default 10). Must correspond precisely to the number of day needed to cover the recent period whose we want informations.

#     Returns:
#         A list of WhatsNewGroupResult objects, one for each type of important event that happened in the asked time period.
#     """
#     print("TOOL_CALL singulair_what_s_up", look_back_days)

#     req_str = " \
#         SELECT DISTINCT sites.id, sites.name, CAST(_inspected.date AS VARCHAR), fir_causes.label_en, _incident_records.id \
#         FROM (SELECT * FROM inspections_status WHERE status_id = 4 AND date > (NOW() - INTERVAL %s DAY) ) _inspected \
#         JOIN turbines ON turbines.id = _inspected.turbine_id \
#         JOIN sites ON sites.id = turbines.site_id \
#         JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = sites.id \
#         LEFT JOIN (SELECT * FROM fir_records WHERE deleted_at IS NULL) _firs \
#         ON _firs.planification_id = _inspected.planification_id AND _firs.turbine_id = _inspected.turbine_id \
#         LEFT JOIN fir_causes ON fir_causes.id = _firs.cause_id \
#         LEFT JOIN (SELECT * FROM incident_records WHERE deleted_at IS NULL AND criticality_id = 5) _incident_records \
#         ON _incident_records.planification_id = _inspected.planification_id AND _incident_records.turbine_id = _inspected.turbine_id \
#         WHERE (_incident_records.dismissed IS NULL OR _incident_records.dismissed = FALSE) \
#         AND (_incident_records.decision_id IS NULL OR _incident_records.decision_id != 0) \
#         AND _incident_records.deleted_at IS NULL \
#         AND _firs.cause_id IS NOT NULL OR _incident_records.criticality_id IS NOT NULL \
#         ORDER BY CAST(_inspected.date AS VARCHAR) DESC \
#     "
    
#     #TODO adapter à ENERCON
    
#     req_params = [str(look_back_days), GLOBAL_INFOS['CURRENT_COMPANY_ID']]

#     print("--- REQ", req_str, req_params)
#     TW_DB_CURSOR.execute(req_str, req_params)

#     group_result_dict = {}
#     for row in TW_DB_CURSOR:
#         site_id, site_name, date, cause, damage_id = row

#         if damage_id is not None:
#             event_type = "Severity 5"
#         else:
#             if cause == "Quality":
#                 event_type = "Rework"
#             else:
#                 event_type = "FIR"

#         if event_type not in group_result_dict:
#             group_result_dict[event_type] = WhatsNewGroupResult(
#                 event_type=event_type,
#                 total_count=0,
#                 total_detailed=0,
#                 detailed_events=[],
#             )
#         if group_result_dict[event_type].total_detailed < 20:
#             group_result_dict[event_type].detailed_events.append(
#                 ImportantEvent(
#                     event_site=Site(id=site_id, name=site_name),
#                     event_date=date.split(" ")[0],
#                     damage_id=damage_id,
#                 )
#             )
#             group_result_dict[event_type].total_detailed += 1
#         group_result_dict[event_type].total_count += 1

#     group_result = []
#     for event_type in group_result_dict:
#         group_result.append(group_result_dict[event_type])

#     print("--- RES", group_result)
#     return str(group_result)

@tool
def get_site_inspections(site_id: int, client_id: int | None = None) -> str:
    """List all inspections for a site.
    
    Args:
        site_id: Identifier of the site. Use get_sites to resolve a site name into its id.
        client_id: Restrict to the inspections of a specific client company (Singulair user only).

    Returns:
        A list wit all Inspections for this site, from the first to the last.
    """
    print("TOOL_CALL get_site_inspections", site_id, client_id)
    
    req_str = " \
        SELECT planifications.id, planifications.date::TEXT \
        FROM planifications \
        JOIN sites ON planifications.site_id = sites.id \
        WHERE planifications.deleted_at IS NULL AND planifications.date IS NOT NULL AND sites.id = %s \
    "
    req_params = [site_id]
    
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] != 5:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(GLOBAL_INFOS['CURRENT_COMPANY_ID'])
    elif client_id is not None:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(client_id)
    
    req_str += "ORDER BY planifications.date DESC "
    
    print("-- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    site_inspections = []
    for row in TW_DB_CURSOR:
        planif_id, planif_date = row
        
        site_inspections.append(Inspection(
            id=planif_id, 
            date=planif_date,
            published_date=str(get_publish_date(planif_id))
        ))
    
    resp = TypeAdapter(list[Inspection]).dump_json(site_inspections).decode(); print("--- RES ", resp)
    return resp

def month_range(year: int, month_number: int) -> tuple[str, str]:
    start_date_str = str(year) + "-" + str(month_number) + "-01"
    end_date_str = str(year + (month_number == 12)) + "-" + str(month_number % 12 + 1) + "-01"
    return start_date_str, end_date_str

@tool
def get_month_inspections(
year: int, month_number: int, 
month_end_detailed: Literal['beginning', 'end'] = 'end',
country_id: str | None = None, client_id: int | None = None
) -> str:
    """List the inspections done during one given month.
    
    Use this when the user names a month or a year rather than a recent window.
    
    Args:
        year: Four-digit year of the month to list, e.g. 2026.
        month_number: Month to list, from 1 (January) to 12 (December).
        month_end_detailed: Which end of the month is detailed if there is too much inspections
        country_id: Restrict to one country
        client_id: Restrict to the inspections of a specific client company (Singulair user only).

    Returns:
        A MonthInspectionsResult holding the total number of inspections that
        month, and the details of the ones covered by month_end_detailed
    """
    print("TOOL_CALL get_month_inspections", year, month_number, month_end_detailed, country_id, client_id)
    
    req_str = " \
        SELECT planifications.id, planifications.date::TEXT, sites.id, sites.name \
        FROM planifications \
        JOIN sites ON planifications.site_id = sites.id \
        WHERE planifications.deleted_at IS NULL AND planifications.date IS NOT NULL \
    "
    req_params = []
    
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] != 5:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(GLOBAL_INFOS['CURRENT_COMPANY_ID'])
    elif client_id is not None:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(client_id)
    
    req_str += "AND date >= DATE  %s AND date < DATE %s "
    month_range_values = month_range(year, month_number)
    req_params.append(month_range_values[0]); req_params.append(month_range_values[1])
    
    if country_id is not None:
        req_str += "AND sites.country_id = %s "; 
        req_params.append(country_id) #TODO add filter in MonthInspectionsResult
    
    if month_end_detailed == 'beginning':
        req_str += "ORDER BY planifications.date ASC "
    elif month_end_detailed == 'end':
        req_str += "ORDER BY planifications.date DESC "
        
    print("-- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    result = MonthInspectionsResult(
        year=year,
        month_number=month_number,
        total_count=0,
        total_detailed=0,
        month_end_detailed=month_end_detailed,
        detailed_inspections=[]
    )
    
    for row in TW_DB_CURSOR:
        planif_id, planif_date, site_id, site_name = row
        
        if result.total_detailed < 50:
            result.detailed_inspections.append(
                Inspection(
                    id=planif_id,
                    date=planif_date,
                    published_date=str(get_publish_date(planif_id)),
                    site=Site(id=site_id, name=site_name)
                )
            )
            result.total_detailed += 1
        result.total_count += 1
        
    resp = TypeAdapter(MonthInspectionsResult).dump_json(result).decode(); print("--- RES ", resp)
    return resp

@tool
def get_recent_inspections(
search_mode: Literal["last_days", "last_inspections"],
search_range: int,
country_id: str | None = None, client_id: int | None = None
) -> str:
    """Give id and dates of the recent planned inspections
    
    Use this tool if the user ask to list the recent planned inspections.
    If the user want details about what happened or important events use the what_s_up tool instead.
    
    Args:
        search_mode: Define if the search_range is in days or in number of inspection
        search_range: Range in which we want the inspections
        country_id: Restrict to one country
        client_id: Restrict to the inspections of a specific client company (Singulair user only).

    Returns:
        A RecentInspectionsResult holding the total number of inspections in the asked range
    """
    print("TOOL_CALL get_recent_inspections", search_mode, search_range, country_id, client_id)

    req_str = " \
        SELECT planifications.id, planifications.date::TEXT, sites.id, sites.name \
        FROM planifications \
        JOIN sites ON planifications.site_id = sites.id \
        WHERE planifications.deleted_at IS NULL AND planifications.date IS NOT NULL \
    "
    req_params = []
    
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] != 5:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(GLOBAL_INFOS['CURRENT_COMPANY_ID'])
    elif client_id is not None:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(client_id)

    if country_id is not None:
        req_str += "AND sites.country_id = %s "; 
        req_params.append(country_id) #TODO add filter in RecentInspectionsResult

    if search_mode == 'last_days':
        req_str += "AND planifications.date >= CURRENT_DATE - %s * INTERVAL '1 day' "
        req_params.append(search_range)

    req_str += "ORDER BY planifications.date DESC "

    if search_mode == 'last_inspections':
        req_str += "LIMIT %s "
        req_params.append(search_range)

    print("-- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)

    result = RecentInspectionsResult(
        total_count=0,
        total_detailed=0,
        detailed_inspections=[]
    )

    for row in TW_DB_CURSOR:
        planif_id, planif_date, site_id, site_name = row

        if result.total_detailed < 50:
            result.detailed_inspections.append(
                Inspection(
                    id=planif_id,
                    date=planif_date,
                    published_date=str(get_publish_date(planif_id)),
                    site=Site(id=site_id, name=site_name)
                )
            )
            result.total_detailed += 1
        result.total_count += 1

    resp = TypeAdapter(RecentInspectionsResult).dump_json(result).decode(); print("--- RES ", resp)
    return resp

@tool
def prioritize_campaign_repair(
    campaign_year: int,
    damage_defect_type_id: int | list[int],
    country_ids: list[int] | None, client_id: int | None = None,
    how_many_to_prioritize: int = 15,
    severity_id: int | None = None,
) -> str:
    """Rank the sites to prioritize for repair after an inspection campaign.

    Use this when the user asks which sites to repair first, how to plan a repair campaign.
    Sites are ranked by how many damages were found on their most
    recently inspected turbines, and each one comes with its total AEP loss —
    the energy the site is losing — so the ranking can be argued on damage count
    and on lost production together. 
    The most important damages for priorization are cracks and erosion.
    
    Never decides for the user, but instead makes reasoned recommendations
    You can ask him what are the most important damages to focus.
    
    You can either Use it if the user ask you for the site with the most X damages with the same kind of parameters
    
    Args:
        campaign_year: The inspection campaign to look at, as a year. Use the
            current year unless the user names another one.
        country_id: a list of country to filter on. Use get_country_ids_by_name to resolve a
            country name into its id. Never guess this value.
        client_id: Restrict to the inspections of a specific client company (Singulair user only).
        damage_defect_type_id: One defect type id, or a list of them.
        how_many_to_prioritize: How many sites to return.
        severity_id: Only counts damages at this severity level or above. 
            Only counts damages at this severity level or above.
    Returns:
        A ranked list of SiteToRepair object, worst first, each with the site to repair and the informations 
        about why it must be repaired in priority.
    """
    print(
        "TOOL_CALL prioritize_campaign_repair",
        campaign_year,
        country_ids, client_id,
        damage_defect_type_id,
        how_many_to_prioritize,
        severity_id,
    )
    
    req_str = " \
        WITH scope AS ( \
            SELECT planifications.id AS planification_id, \
                planifications.date, \
                sites.id          AS site_id, \
                sites.name        AS site_name \
            FROM planifications \
            JOIN sites ON sites.id = planifications.site_id \
            JOIN societes ON societes.id = planifications.societe_id \
            JOIN schedule_inspections ON schedule_inspections.planification_id = planifications.id \
            WHERE schedule_inspections.campaign = %s \
            AND planifications.deleted_at IS NULL \
    "
    req_params = [campaign_year]
        
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] != 5:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(GLOBAL_INFOS['CURRENT_COMPANY_ID'])
    elif client_id is not None:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(client_id)
    
    if country_ids :
        req_str += "AND sites.country_id = ANY (%s) "
        req_params.append(country_ids)
    
    req_str += " \
            GROUP BY planifications.id, planifications.date, sites.id, sites.name \
        ), \
        turbine_latest AS ( \
            SELECT DISTINCT ON (asset_scope.turbine_id) \
                asset_scope.turbine_id, \
                scope.planification_id, \
                scope.site_id, \
                scope.site_name \
            FROM (SELECT DISTINCT turbine_id, planification_id \
                FROM asset_scope \
                WHERE deleted_at IS NULL) asset_scope \
            JOIN scope ON scope.planification_id = asset_scope.planification_id \
            ORDER BY asset_scope.turbine_id, scope.date DESC, scope.planification_id DESC \
        ), \
        \
        damages AS ( \
            SELECT turbine_latest.site_id, \
                turbine_latest.site_name, \
                ROUND(COUNT(incident_records.id)::numeric \
                        / NULLIF(COUNT(DISTINCT turbine_latest.turbine_id), 0), \
                        2 \
                        ) AS avg_damages_per_turbine,\
                COUNT(DISTINCT turbine_latest.turbine_id) AS nb_turbines, \
                COUNT(incident_records.id)                AS nb_damages, \
                ARRAY_AGG(DISTINCT turbine_latest.planification_id \
                            ORDER BY turbine_latest.planification_id) AS planification_ids \
            FROM turbine_latest \
            LEFT JOIN incident_records \
                ON incident_records.turbine_id = turbine_latest.turbine_id \
                AND incident_records.planification_id = turbine_latest.planification_id \
                AND incident_records.deleted_at IS NULL \
                AND incident_records.dismissed = FALSE \
                AND incident_records.decision_id != 0 \
    "
    
    is_enercon_data = GLOBAL_INFOS["CURRENT_COMPANY_ID"] == 15 or client_id == 15
    
    if is_enercon_data: req_str += "AND incident_records.priority_id <> 5 "

    defect_type_ids = (
        damage_defect_type_id
        if isinstance(damage_defect_type_id, list)
        else [damage_defect_type_id]
    )
    if is_enercon_data and (113 in defect_type_ids or 121 in defect_type_ids or 123 in defect_type_ids or 124 in defect_type_ids):
        req_str += "AND ((incident_records.component_id IN (70, 71, 84) \
            AND incident_records.defect_type_id IN (113, 121, 123, 124)) "
        defect_type_ids.extend([113, 121, 123, 124])
        defect_type_ids = list(set(defect_type_ids))
        if len(defect_type_ids) > 4:
            req_str += "OR incident_records.defect_type_id = ANY(%s)) "
        else: req_str += ")"            
        del defect_type_ids[defect_type_ids.index(113)]; del defect_type_ids[defect_type_ids.index(121)]; del defect_type_ids[defect_type_ids.index(123)]; del defect_type_ids[defect_type_ids.index(124)]
    else:
        req_str += " AND incident_records.defect_type_id = ANY(%s) "
    
    if len(defect_type_ids) > 0: # Cond only for ENERCON
        req_params.append(defect_type_ids)
        
    if severity_id:
        if is_enercon_data:
            req_str += " AND incident_records.criticality_id <= %s "
        elif severity_id:
            req_str += " AND incident_records.criticality_id >= %s "
        req_params.append(severity_id)
        
    if is_enercon_data:
        req_str += "\
            GROUP BY turbine_latest.site_id, turbine_latest.site_name \
            ) \
            SELECT damages.site_id, \
                damages.site_name, \
                damages.nb_turbines, \
                damages.nb_damages, \
                damages.avg_damages_per_turbine,\
                damages.planification_ids \
            FROM damages \
            WHERE damages.nb_damages > 1 \
            ORDER BY damages.nb_damages DESC \
            LIMIT %s \
        "
        req_params.append(how_many_to_prioritize)

        if is_enercon_data: req_str = req_str.replace("criticality_id", "priority_id")
        print("--- REQ", req_str, req_params)
        TW_DB_CURSOR.execute(req_str, req_params)
        rows = TW_DB_CURSOR.fetchall()

        if not rows:
            return "No site found for this campaign, country and damage type."

        sites = []
        for (
            site_id,
            site_name,
            nb_turbines,
            nb_damages,
            avg_per_turbine,
            planification_ids,
        ) in rows:
            sites.append(
                SiteToRepair(
                    site=Site(id=site_id, name=site_name),
                    number_of_turbines= nb_turbines,
                    number_of_damages= nb_damages,
                    average_damages_per_turbine= (
                        float(avg_per_turbine) if avg_per_turbine is not None else None
                    ),
                    inspection= [Inspection(id=planification_id, published_date=get_publish_date(planification_id)) for planification_id in planification_ids],
                )
            )
    else:
        req_str += " \
            GROUP BY turbine_latest.site_id, turbine_latest.site_name \
            ), \
                aep AS (\
            SELECT turbine_latest.site_id,\
                SUM(al.aep_loss_jensen)        AS aep_loss_site,\
                COUNT(DISTINCT al.turbine_id)  AS nb_turbines_avec_aep\
            FROM turbine_latest\
            JOIN (\
                SELECT DISTINCT ON (turbine_id, planification_id)\
                    turbine_id, planification_id, aep_loss_jensen\
                FROM aep_loss\
                ORDER BY turbine_id, planification_id\
            ) al\
            ON al.turbine_id = turbine_latest.turbine_id\
            AND al.planification_id = turbine_latest.planification_id\
            GROUP BY turbine_latest.site_id\
        )\
            SELECT damages.site_id, \
                damages.site_name, \
                damages.nb_turbines, \
                damages.nb_damages, \
                damages.avg_damages_per_turbine,\
                aep.aep_loss_site, \
                aep.nb_turbines_avec_aep, \
                damages.planification_ids \
            FROM damages \
            LEFT JOIN aep ON aep.site_id = damages.site_id \
            WHERE damages.nb_damages > 1 \
            ORDER BY damages.nb_damages DESC \
            LIMIT %s \
        "
        req_params.append(how_many_to_prioritize)

        print("--- REQ", req_str, req_params)
        TW_DB_CURSOR.execute(req_str, req_params)
        rows = TW_DB_CURSOR.fetchall()

        if not rows:
            return "No site found for this campaign, country and damage type."

        sites = []
        
        for (
            site_id, site_name,
            nb_turbines,
            nb_damages,
            avg_per_turbine,
            aep_loss_site,
            nb_with_aep,
            planification_ids,
        ) in rows:
            sites.append(
                SiteToRepair(
                    site=Site(id=site_id, name=site_name),
                    number_of_turbines=nb_turbines,
                    number_of_damages=nb_damages,
                    average_damages_per_turbine=(
                        float(avg_per_turbine) if avg_per_turbine is not None else None
                    ),
                    aep_loss_site= SiteAEPLoss(
                        site=Site(id=site_id, name=site_name),
                        aep_loss_site=float(aep_loss_site) if aep_loss_site is not None else 0
                    ),
                    number_of_turbines_with_AEP= nb_with_aep if nb_with_aep is not None else 0,
                    inspection= [Inspection(id=planification_id,published_date=get_publish_date(planification_id)) for planification_id in planification_ids],
                )
            )
    
    resp = TypeAdapter(list[SiteToRepair]).dump_json(sites).decode(); print("--- RES ", resp)
    return resp


@tool
def manufacturer_damage_stats(defect_type_ids: list[int], component_ids: list[int] | None, model_name: str | None):
    """
    Rank Enercon blade manufacturers by damage rate for a given damage.
    ALWAYS call this tool when the user asks about the cause, origin or reason of a damage
    (e.g. "what could cause white cracks on the E138 preforms?"), or if a damage is recurrent
    or linked to a blade manufacturer. A manufacturer with an abnormal damage rate is a
    possible cause to point out.

    Args:
        defect_type_ids: IDs of the defect types. A damage name can have several IDs,
            so include all IDs with the same or similar name (e.g. all "white crack").
        component_ids: IDs of the damaged components (e.g. all "preform"; same rule,
            include all matching IDs). None = all components.
        model_name: Full or partial model label, separators ignored ("E138" = "E-138").
            None = all models.

    Returns one row per manufacturer, ordered by dmg_quantity:
        - modeles: matched model labels
        - constructor: blade manufacturer (serial number prefix)
        - dmg_quantity: number of damages found
        - pourcentage: share of all damages (sums to 100%)
        - nb_turbines_endommagees: damaged turbines
        - nb_turbines_total: all turbines with this manufacturer's blades
        - pourcentage_turbines_touchees: damage rate, use this to compare manufacturers

    How to answer:
        - Show a pie chart of the damage share per manufacturer (pourcentage),
          along with the usual answer (table or summary of the results).
        - Draw conclusions: point out irregularities such as a manufacturer with a
          much higher damage rate than the others, which may suggest a manufacturer defect.
        - Flag results based on few turbines (e.g. 2 out of 2 = 100%) as not reliable.
        - If no manufacturer stands out, say the data does not point to a manufacturer
          defect and that other causes should be considered.
    """
    print("TOOL_CALL manufacturer_damage_stats", defect_type_ids, component_ids, model_name)
    
    req_params=[]
    req_str="""WITH m AS (
        SELECT id, NAME
        FROM models
    """
    
    if model_name:
        req_str+="""
    WHERE REGEXP_REPLACE(NAME, '[^a-zA-Z0-9]', '', 'g') ILIKE %s"""
        req_params.append(f'%{model_name}%')
    req_str+="""),
        blades AS (
            SELECT
                c.turbine_id,
                COALESCE(NULLIF(SUBSTRING(c.blade_serial FROM '^[^0-9]+'), ''), '(aucun)') AS constructor
            FROM components_turbines_enercon c
            JOIN turbines t ON t.id = c.turbine_id
            JOIN m ON m.id = t.model_name
        ),
        inc AS (
            SELECT ir.turbine_id, COUNT(*) AS n_inc
            FROM incident_records ir
            WHERE ir.defect_type_id = ANY (%s)
    """
    
    req_params.append(defect_type_ids)
    if component_ids:
        req_str+="AND ir.component_id = ANY (%s)"
        req_params.append(component_ids)
    req_str+="""
            AND ir.deleted_at IS NULL
            AND ir.turbine_id IN (SELECT turbine_id FROM blades)
            GROUP BY ir.turbine_id
        ),
        tot AS (
            SELECT constructor, COUNT(DISTINCT turbine_id) AS nb_turbines_total
            FROM blades
            GROUP BY constructor
        )
        SELECT
            (SELECT STRING_AGG(DISTINCT NAME, ', ') FROM m) AS modeles,
            b.constructor,
            SUM(i.n_inc) AS dmg_quantity,
            ROUND(SUM(i.n_inc) * 100.0 / SUM(SUM(i.n_inc)) OVER (), 2) AS pourcentage_with_this_damage,
            COUNT(DISTINCT b.turbine_id) AS nb_turbines_endommagees,
            tot.nb_turbines_total,
            ROUND(COUNT(DISTINCT b.turbine_id) * 100.0 / tot.nb_turbines_total, 2) AS pourcentage_turbines_touchees
        FROM blades b
        JOIN inc i ON i.turbine_id = b.turbine_id
        JOIN tot ON tot.constructor = b.constructor
        GROUP BY b.constructor, tot.nb_turbines_total
        ORDER BY dmg_quantity DESC
    """

    TW_DB_CURSOR.execute(req_str,req_params)
    manufacturer_damage_tab=[]
    for row in TW_DB_CURSOR:
        (
            model,
            constructor,
            dmg_quantity,
            percent_with_damage,
            nb_damaged_turbine,
            total_turbine_constructor,
            percent_total_turbine_constructor,
        ) = row
        manufacturer_damage_tab.append(
            ManufacturerDamages(
                manufacturer_name=constructor,
                turbine_models=model,
                damage_quantity=dmg_quantity,
                percentage_with_this_damage=percent_with_damage,
                number_of_turbines_with_this_damage=nb_damaged_turbine,
                number_total_of_turbine=total_turbine_constructor,
                percentage_of_turbines_with_this_damage_from_contructor=percent_total_turbine_constructor,
            )
        )

    resp = TypeAdapter(list[ManufacturerDamages]).dump_json(manufacturer_damage_tab).decode(); print("--- RES ", resp)
    return resp