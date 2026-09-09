import json
from re import I

from langchain_core.tools import tool
from maia_utils import *

@tool
def get_wind_index_explanation(specific_wind_index: str | None = None) -> str:
    """Explain what the WIND index is and how to read it.

    Call this before interpreting or commenting on any wind index value, so that
    the explanation given to the user matches the definition used in turbinewatch.

    Args:
        wind_index: Name of one specific index, to get its own description.
        If omitted, returns a general explanation of the WIND index.

    Returns:
        A plain-text explanation, meant to be relayed to the user.
    """
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

        if specific_wind_index == "C1":
            return "This damage is cosmetic and won't worsen."
        elif specific_wind_index == "C2":
            return "This damage will likely worsen but remaining cosmetic."
        elif specific_wind_index == "C3":
            return "This damage is cosmetic but could worsen and become medium."
        elif specific_wind_index == "M1":
            return "This damage affect blade efficiency but won't worsen."
        elif specific_wind_index == "M2":
            return "This damage will worsen, which is bad for blade efficiency."
        elif specific_wind_index == "M3":
            return "This damage is not severe yet but could worsen in this way."
        elif specific_wind_index == "S1":
            return "This type of damage is dangerous but likely don't worsen."
        elif specific_wind_index == "S2":
            return "This damage is dangerous and will likely worsen."
        elif specific_wind_index == "S3":
            return "A blade failure could happen if this damage is not repaired."
        elif specific_wind_index == "F":
            return "The blade is broken (Failure)."
        else:
            return "specific_wind_index not valid"

@tool
def filter_report(
campaign_year: int | None = None,
country_id: int | None = None,
inspection_type_id: int | None = None
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
    Returns:
        A fenced block tagged "filter" containing the filters as JSON, to be copied
        verbatim into your answer.
    """
    print("TOOL_CALL filter_report", campaign_year, country_id, inspection_type_id)
    
    filters = {
        "campaign_year": campaign_year,
        "country_id": country_id,
        "inspection_type_id": inspection_type_id,
    }
    filters = {k: v for k, v in filters.items() if v is not None}

    if not filters:
        resp = "No filter was provided. Ask the user which campaign, country or inspection type they want."; print("--- RES", resp)
        return resp

    resp = "```filter\n" + json.dumps(filters, indent=2) + "\n```"; print("--- RES", resp)
    return resp

# @tool
# def service_manager_what_s_up(TODO) -> str:
#     """TODO"""
#     pass

@tool
def singulair_what_s_up(look_back_days: int = 10) -> str:
    """Summarize the important events detected across the monitored wind farms over a recent period.

    Use this for open-ended status questions: "what's up", "what is important to know", "any alerts?", "brief me on the fleet".

    Event types:
    - FIR (Failed Inspection Report): an inspection could not be carried out because of an issue.
    - Rework: a turbine must be inspected again because of image quality problems.
    - Severity 5: an inspection revealed a severity 5 damage, the most critical level.

    Args:
        look_back_days: size of the look-back window, in days (default 10). Must correspond precisely to the number of day needed to cover the recent period whose we want informations.

    Returns:
        A list of WhatsNewGroupResult objects, one for each type of important event that happened in the asked time period.
    """
    print("TOOL_CALL singulair_what_s_up", look_back_days)

    req_str = " \
        SELECT DISTINCT sites.id, sites.name, CAST(_inspected.date AS VARCHAR), fir_causes.label_en, _incident_records.id \
        FROM (SELECT * FROM inspections_status WHERE status_id = 4 AND date > (NOW() - INTERVAL %s DAY) ) _inspected \
        JOIN turbines ON turbines.id = _inspected.turbine_id \
        JOIN sites ON sites.id = turbines.site_id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = sites.id \
        LEFT JOIN (SELECT * FROM fir_records WHERE deleted_at IS NULL) _firs \
        ON _firs.planification_id = _inspected.planification_id AND _firs.turbine_id = _inspected.turbine_id \
        LEFT JOIN fir_causes ON fir_causes.id = _firs.cause_id \
        LEFT JOIN (SELECT * FROM incident_records WHERE deleted_at IS NULL AND criticality_id = 5) _incident_records \
        ON _incident_records.planification_id = _inspected.planification_id AND _incident_records.turbine_id = _inspected.turbine_id \
        WHERE (_incident_records.dismissed IS NULL OR _incident_records.dismissed = FALSE) \
        AND (_incident_records.decision_id IS NULL OR _incident_records.decision_id != 0) \
        AND _incident_records.deleted_at IS NULL \
        AND _firs.cause_id IS NOT NULL OR _incident_records.criticality_id IS NOT NULL \
        ORDER BY CAST(_inspected.date AS VARCHAR) DESC \
    "
    
    req_params = [str(look_back_days), GLOBAL_INFOS['CURRENT_COMPANY_ID']]

    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)

    group_result_dict = {}
    for row in TW_DB_CURSOR:
        site_id, site_name, date, cause, damage_id = row

        if damage_id is not None:
            event_type = "Severity 5"
        else:
            if cause == "Quality":
                event_type = "Rework"
            else:
                event_type = "FIR"

        if event_type not in group_result_dict:
            group_result_dict[event_type] = WhatsNewGroupResult(
                event_type=event_type,
                total_count=0,
                total_detailed=0,
                detailed_events=[],
            )
        if group_result_dict[event_type].total_detailed < 20:
            group_result_dict[event_type].detailed_events.append(
                ImportantEvent(
                    event_site=Site(id=site_id, name=site_name),
                    event_date=date.split(" ")[0],
                    damage_id=damage_id,
                )
            )
            group_result_dict[event_type].total_detailed += 1
        group_result_dict[event_type].total_count += 1

    group_result = []
    for event_type in group_result_dict:
        group_result.append(group_result_dict[event_type])

    print("--- RES", group_result)
    return str(group_result)

@tool
def get_site_inspections(site_id: int) -> str:
    """List all inspections for a site.
    
    Args:
        site_id: Identifier of the site. Use get_sites to resolve a site name into its id.

    Returns:
        A list wit all Inspections for this site, from the first to the last.
    """
    print("TOOL_CALL get_site_inspections", site_id)
    
    req_str = " \
        SELECT planifications.id, planifications.date::TEXT \
        FROM planifications \
        JOIN sites ON planifications.site_id = sites.id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = sites.id \
        WHERE planifications.deleted_at IS NULL AND planifications.date IS NOT NULL AND sites.id = %s \
        ORDER BY planifications.date DESC \
    "
    
    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID'], site_id]
    
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
    
    resp = str(site_inspections); print("-- RES", resp)
    return resp

def month_range(year: int, month_number: int) -> tuple[str, str]:
    start_date_str = str(year) + "-" + str(month_number) + "-01"
    end_date_str = str(year + (month_number == 12)) + "-" + str(month_number % 12 + 1) + "-01"
    return start_date_str, end_date_str

@tool
def get_month_inspections(
year: int, month_number: int, 
month_end_detailed: Literal['beginning', 'end'] = 'end',
country_id: str | None = None
) -> str:
    """List the inspections done during one given month.
    
    Use this when the user names a month or a year rather than a recent window.
    
    Args:
        year: Four-digit year of the month to list, e.g. 2026.
        month_number: Month to list, from 1 (January) to 12 (December).
        month_end_detailed: Which end of the month is detailed if there is too much inspections
        country_id: Restrict to one country

    Returns:
        A MonthInspectionsResult holding the total number of inspections that
        month, and the details of the ones covered by month_end_detailed
    """
    print("TOOL_CALL get_month_inspections", year, month_number, month_end_detailed, country_id)
    
    req_str = " \
        SELECT planifications.id, planifications.date::TEXT, sites.id, sites.name \
        FROM planifications \
        JOIN sites ON planifications.site_id = sites.id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = sites.id \
        WHERE planifications.deleted_at IS NULL AND planifications.date IS NOT NULL \
    "
    
    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID']]
    
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
    
    print("--- RES", result)
    return str(result)

@tool
def get_recent_inspections(
search_mode: Literal["last_days", "last_inspections"],
search_range: int,
country_id: str | None = None
) -> str:
    """List recent inspections.
    
    Args:
        search_mode: Define if the search_range is in days or in number of inspection
        search_range: Range in which we want the inspections
        country_id: Restrict to one country

    Returns:
        A RecentInspectionsResult holding the total number of inspections in the asked range
    """
    print("TOOL_CALL get_recent_inspections", search_mode, search_range, country_id)

    req_str = " \
        SELECT planifications.id, planifications.date::TEXT, sites.id, sites.name \
        FROM planifications \
        JOIN sites ON planifications.site_id = sites.id \
        JOIN (SELECT site_id FROM societes_sites WHERE societe_id = %s) _societes_sites ON _societes_sites.site_id = sites.id \
        WHERE planifications.deleted_at IS NULL AND planifications.date IS NOT NULL \
    "

    req_params = [GLOBAL_INFOS['CURRENT_COMPANY_ID']]

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

    print("--- RES", result)
    return str(result)

# @tool
# def get_inspections(
# search_mode: Literal["last_X_days", "last_X_inspections", "period"],
# search_range: int,
# date_range: list | None = None,
# country_id: str | None = None,
# site_name: str | None = None,
# operateur_label_en: str | None = None,
# ) -> str:
#     """List completed site inspections, with the site and the campaign behind each one.

#     Use this whenever the question is about inspection activity rather than about
#     the content of one inspection: what was inspected lately, which sites a given
#     operator had inspected, what happened in a country over the past weeks, or
#     what was inspected during a given month. Typical questions: "which sites did
#     we inspect this month", "what are the latest inspections for <operator>",
#     "show me the last 10 inspections in Spain", "what did we inspect in March
#     2026".
#     For more general questions like "What happened this month?" use the
#     singulair_what_s_up tool instead.

#     It is also how you find a planification id when the user refers to "the
#     latest inspection" or "the last campaign" without naming one: call this tool,
#     take the planification id from the entry you need, and pass it to the tools
#     that require a planification. This applies to the CURRENT campaign only —
#     never use it to hunt down a previous one for comparison.

#     Only inspections whose report has been published are returned. Campaigns
#     still being analysed are left out — so a site the user knows was recently
#     flown may legitimately be absent if its report is not published yet.

#     Args:
#         search_mode: How to limit the result. Use "last_X_days" for a rolling
#             time window — everything published in the last X days, which may be
#             nothing at all if the period was quiet. Use "last_X_inspections" for
#             a fixed count — the X most recent inspections. Pick the one that
#             matches the question: "what was inspected this week" is a time
#             window, "show me the last 5 inspections" is a count.
#             Required in every case, including when date_range is given.
#         search_range: The number of days in "last_X_days" mode, the number of
#             inspections in "last_X_inspections" mode. Required in both cases.
#             In count mode the search never goes back further than about three
#             years, so asking for more inspections than exist in that window
#             simply returns fewer.
#         date_range: Optional. A list of three values:
#             ["YYYY-MM-DD", "YYYY-MM-DD", "asc" | "desc"]. Start date first, end
#             date second, both included, sort direction third — all three are
#             required when you use this argument.
#             Use it whenever the user names a period with a beginning and an end
#             rather than a recent window: a month ("March 2026" is
#             ["2026-03-01", "2026-03-31", "desc"]), a quarter, a year, or two
#             explicit dates.

#             The third value decides which end of the period you get, and the
#             user's own wording decides it for you:
#             - "first", "earliest", "oldest", "beginning of" -> "asc"
#             - "last", "latest", "most recent", "end of" -> "desc"
#             - no wording either way -> "desc"
#             This rule wins over any preference for one direction. Asking for the
#             first N inspections and returning the last N answers a different
#             question.

#             Examples, all over March 2026:
#             - "the first 30 inspections of March" ->
#               ["2026-03-01", "2026-03-31", "asc"], search_range 30
#             - "the last 30 inspections of March" ->
#               ["2026-03-01", "2026-03-31", "desc"], search_range 30
#             - "what did we inspect in March" ->
#               ["2026-03-01", "2026-03-31", "desc"], search_range 100

#             When date_range is given it replaces the rolling window entirely:
#             search_mode and search_range then only decide how many results come
#             back, not how far back to look.
#             To get EVERY inspection of the period, pass search_mode
#             "last_X_inspections" with search_range 100. Anything above 100 is
#             refused by the tool, so 100 is the way to ask for "all of them".
#         country_id: country_id: Restrict to one country.
#         site_name: Restricts the result to one site, by name. Use it
#             only when the user explicitly asks about that site — "what did we
#             inspect at <site>", "when was <site> last inspected".
#             Never use it to look up the previous campaign of a site. The tools
#             that compare two campaigns — analyse_crack_evolution,
#             analyse_erosion_evolution — resolve the previous one themselves from
#             the turbine and the current planification: pass them
#             planification_id and leave previous_planification_id out. Going
#             through this tool to find that id yourself gives the wrong answer,
#             because it ranks by report publication date rather than by the
#             campaign history of that specific turbine.
#             The match is phonetic, so an approximate spelling of what the user
#             said will usually work — but the phonetics are English, so an
#             accented or Spanish site name may not match.
#         operateur_label_en: Optional. Restricts the result to one operator — the
#             company operating the site — by its name. Same phonetic matching as
#             site_name. Use it for questions like "what did we inspect for
#             <operator> recently".

#     Returns:
#         One entry per inspection, with the planification (id and date), the site
#         (id and name), and published_date, the date the inspection report was
#         published by the blade analyst.

#         The planification date and the analyze date differ, and both matter: the
#         first is when the campaign was set up, the second when the analysis was
#         actually delivered.

#         A site may appear several times if it was inspected more than once in the
#         period. Filters combine: passing both a country and an operator returns
#         only the inspections matching both.

#         When more than 100 inspections match, no list is returned at all — only a
#         message asking to narrow the search. In that case, suggest a shorter
#         period or a country, site or operator filter rather than retrying with
#         the same arguments.
#     """
#     print("TOOL_CALL get_inspections", search_mode, search_range, date_range, country_id, site_name, operateur_label_en)

#     # ------------------------------------------------------------------ #
#     # 1. Requête principale : base CB
#     # ------------------------------------------------------------------ #
#     req_params = []
#     req_str = (
#         "SELECT pid, sid, snam, published_date "
#         "FROM prepared_reports_by_campaign "
#         "WHERE pstat IN ('Published','Sent to CIR','Analyzed','Validated') "
#         "AND published_date IS NOT NULL"
#     )

#     if date_range:
#         if len(date_range) != 3:
#             return ("ERROR: date_range must hold three values: start date, "
#                     "end date, and \"asc\" or \"desc\"")
#         if str(date_range[2]).lower() not in ("asc", "desc"):
#             return "ERROR: the third value of date_range must be \"asc\" or \"desc\""

#         sort_direction = str(date_range[2]).upper()
#         req_str += (" AND published_date::date >= %s::date"
#                     " AND published_date::date <= %s::date")
#         req_params.append(date_range[0])
#         req_params.append(date_range[1])
#     else:
#         sort_direction = "DESC"
#         req_str += " AND published_date::date >= CURRENT_DATE - %s * INTERVAL '1 day'"
#         req_params.append(search_range if search_mode == "last_X_days" else 1000)

#     if country_id:
#         TW_DB_CURSOR.execute("SELECT label_en FROM countries WHERE id = %s", (country_id,))
#         row = TW_DB_CURSOR.fetchone()
#         country_label = row[0] #TODO manage ERROR if row else None

#         req_str += " AND cnam = %s"
#         req_params.append(country_label)

#     if site_name:
#         req_str += " AND SOUNDEX(snam) = SOUNDEX(%s)"
#         req_params.append(site_name)

#     if operateur_label_en:
#         req_str += " AND SOUNDEX(proj) = SOUNDEX(%s)"
#         req_params.append(operateur_label_en)

#     # sort_direction ne peut valoir que ASC ou DESC : jamais la valeur brute
#     # reçue du modèle. C'est de la structure SQL, pas un paramètre.
#     req_str += f" ORDER BY published_date::date {sort_direction}"

#     if search_mode == "last_X_inspections":
#         req_str += " LIMIT %s"
#         req_params.append(search_range)

#     print("--- REQ", req_str, req_params)
#     CB_DB_CURSOR.execute(req_str, req_params)
#     rows = CB_DB_CURSOR.fetchall()

#     if not rows:
#         return "No published inspection found for this search."

#     if len(rows) > 100:
#         resp = ("There is too much data to analyse.\n", "You can propose to the user to reduce the range.")
#         print("--- RES", resp)
#         return resp

#     # ------------------------------------------------------------------ #
#     # 2. Dates de planification : base TW, en une seule requête
#     #
#     # ANY plutôt qu'une requête par ligne : sur 100 résultats, un aller-retour
#     # au lieu de cent.
#     # ------------------------------------------------------------------ #
#     planification_ids = [row[0] for row in rows]
#     TW_DB_CURSOR.execute(
#         "SELECT id, date FROM planifications WHERE id = ANY(%s)",
#         (planification_ids,),
#     )
#     planification_dates = {row[0]: row[1] for row in TW_DB_CURSOR.fetchall()}

#     # ------------------------------------------------------------------ #
#     # 3. Construction du résultat, dans l'ordre rendu par le SQL
#     # ------------------------------------------------------------------ #
#     date_site_list_dict = []
#     for planification_id, site_id, site_name_, published_date in rows:
#         planification_date = planification_dates.get(planification_id)

#         date_site_list_dict.append({
#             "inspection": Inspection(
#                 id=planification_id,
#                 published_date=str(published_date).split(" ")[0],
#             ),
#             "site": Site(id=site_id, name=site_name_)
#         })

#     print("--- RES", date_site_list_dict)
#     return str(date_site_list_dict)


@tool
def prioritize_campaign_repair(
    campaign_year: int,
    country_id: int,
    damage_defect_type_id: int | list[int],
    how_many_to_prioritize: int = 15,
    severity_id: int | None = None,
) -> str:
    """Rank the sites to prioritize for repair after an inspection campaign.

    Use this when the user asks which sites to repair first, how to plan a repair campaign.
    Sites are ranked by how many damages were found on their most
    recently inspected turbines, and each one comes with its total AEP loss —
    the energy the site is losing — so the ranking can be argued on damage count
    and on lost production together.
    
    Never decides for the user, but instead makes reasoned recommendations
    You can ask him what are the most important damages to focus.

    Leading edge erosion is the default lens for VESTAS Inspections (society_id 3)
    Crack is the default lens for ENERCON Inspections (society_id 15)

    Args:
        campaign_year: The inspection campaign to look at, as a year. Use the
            current year unless the user names another one.
        country_id: Country to filter on. Use get_country_ids_by_name to resolve a
            country name into its id. Never guess this value.
        damage_defect_type_id: One defect type id, or a list of them.
        how_many_to_prioritize: How many sites to return.
        severity_id:  Only counts damages at this severity level or
            above.
    Returns:
        The ranked sites, worst first, each with:
        - site: its id and name
        - nb_damages: how many matching damages were found, which drives the rank
        - nb_turbines: how many turbines were inspected on that site. Compare the
          two: 200 damages over 60 turbines is a different situation from 200 over
          12, and the second is the one to worry about
        - avg_damages_per_turbine: nb_damages divided by nb_turbines. This is
          often the more telling figure: a large site always accumulates more
          damages than a small one, while the average tells you how badly each
          turbine is affected. A site ranked fifth on raw count but first on this
          average is a strong candidate to move up
        - aep_loss_site: the total energy the site is losing, summed over its
          turbines. Use it to argue the priority beyond the raw damage count — a
          site with fewer damages but a higher AEP loss may deserve to come first
        - nb_turbines_with_aep: how many turbines actually have an AEP figure.
          When it is lower than nb_turbines, aep_loss_site only covers part of the
          site and must not be compared as-is with a site where both match
        - planification_ids: the campaigns the figures come from. More than one id
          means the site was inspected in several batches, and these ids are what
          you pass to the detail tools afterwards
    """
    # GLOBAL_INFOS['CURRENT_COMPANY_ID']
    ###
    # Il faut récupérer toutes les inspections de 'pays' par sites
    # Peut etre faire une moyenne des dégats par turbines dans 'pays'
    # récupérer les turbines orphelines et vérifier si elles sont proches (dans un 3eme temps ?)
    # On regarde les sites avec le plus de dégats de cat 2 ou +
    # On peut essayer de filtrer sur l'érosion pour récupérer les 5 sites avec le plus d'érosion ?
    # puis sur ces 5 sites la on récupère l'AEP Loss du site et on l'utilise pour dire on focus ceux la !
    # et voila les sites a priorisé
    # on peut relier les sites géographiquement pour créer des "pools" de sites pour être plus précis pour les
    # réparations et les transports de matériels / personnes
    ###
    print(
        "TOOL_CALL prioritize_campaign_repair",
        campaign_year,
        country_id,
        damage_defect_type_id,
        how_many_to_prioritize,
        severity_id,
    )

    req_params = []
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
    WHERE sites.country_id = %s \
      AND schedule_inspections.campaign = %s \
      AND planifications.deleted_at IS NULL \
      AND societes.id = %s \
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
    "
    # Ici ce sont les valeurs obligatoires donc on va les ajouters directement au req_params
    req_params.append(country_id)
    req_params.append(campaign_year)
    # TODO changer la commande SQL si company ID est SINGULAIR
    society_id= GLOBAL_INFOS["CURRENT_COMPANY_ID"]
    req_params.append(GLOBAL_INFOS["CURRENT_COMPANY_ID"])
    
    req_str += " \
damages AS ( \
    SELECT turbine_latest.site_id, \
           turbine_latest.site_name, \
           ROUND(COUNT(incident_records.id)::numeric \
                / NULLIF(COUNT(DISTINCT turbine_latest.turbine_id), 0), \
                2 \
                )AS avg_damages_per_turbine,\
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

    defect_type_ids = (
        damage_defect_type_id
        if isinstance(damage_defect_type_id, list)
        else [damage_defect_type_id]
    )

    req_str += " AND incident_records.defect_type_id = ANY(%s)"
    req_params.append(defect_type_ids)
    if severity_id:
        req_str += " \
         AND incident_records.criticality_id >%s  \
        "
        req_params.append(severity_id - 1)
    if society_id == 15:
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
                SiteStatus(
                    site=Site(id=site_id, name=site_name),
                    number_of_turbines= nb_turbines,
                    number_of_damages= nb_damages,
                    average_damages_per_turbine= (
                        float(avg_per_turbine) if avg_per_turbine is not None else None
                    ),
                    inspection= [Inspection(id=planification_id,published_date=get_publish_date(planification_id)) for planification_id in planification_ids],
                )
            )
    else:
        req_str += " \
        GROUP BY turbine_latest.site_id, turbine_latest.site_name \
    ), \
        aep AS ( \
        SELECT turbine_latest.site_id, \
            SUM(aep_loss.aep_loss_jensen) AS aep_loss_site, \
            COUNT(aep_loss.turbine_id)    AS nb_turbines_avec_aep \
        FROM turbine_latest \
        JOIN aep_loss \
        ON aep_loss.turbine_id = turbine_latest.turbine_id \
        AND aep_loss.planification_id = turbine_latest.planification_id \
        GROUP BY turbine_latest.site_id \
    ) \
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
            site_id,
            site_name,
            nb_turbines,
            nb_damages,
            avg_per_turbine,
            aep_loss_site,
            nb_with_aep,
            planification_ids,
        ) in rows:
            sites.append(
                SiteStatus(
                    site=Site(id=site_id, name=site_name),
                    number_of_turbines= nb_turbines,
                    number_of_damages= nb_damages,
                    average_damages_per_turbine= (
                        float(avg_per_turbine) if avg_per_turbine is not None else None
                    ),
                    aep_loss_site= SiteAEPLoss(site=Site(id=site_id, name=site_name),
                                               aep_loss_site=float(aep_loss_site) if aep_loss_site is not None else None
                    ),
                    number_of_turbines_with_AEP= nb_with_aep,
                    inspection= [Inspection(id=planification_id,published_date=get_publish_date(planification_id)) for planification_id in planification_ids],
                )
            )

    print("--- RES", sites)
    return str(sites)
