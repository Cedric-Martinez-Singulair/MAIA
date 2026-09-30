from maia_utils import *

def get_recent_severity_5(look_back_days: int = 10, country_id: int | None = None, client_id: int | None = None):
    req_str = " \
        SELECT DISTINCT _inspected.planification_id AS planif_id, CAST(_inspected.date AS VARCHAR) AS planif_date, \
            sites.id AS site_id, sites.name AS site_name, \
            _incident_records.id AS damage_id, defect_types.id AS damage_type_id, defect_types.label_en AS damage_type \
        FROM (SELECT * FROM inspections_status WHERE status_id = 4 AND date > (NOW() - INTERVAL %s DAY) ) _inspected \
        JOIN turbines ON turbines.id = _inspected.turbine_id \
        JOIN sites ON sites.id = turbines.site_id \
        JOIN planifications ON planifications.id = _inspected.planification_id \
        JOIN (SELECT * FROM incident_records WHERE deleted_at IS NULL AND criticality_id = 5) _incident_records \
        JOIN defect_types ON defect_types.id = _incident_records.defect_type_id \
        ON _incident_records.planification_id = _inspected.planification_id AND _incident_records.turbine_id = _inspected.turbine_id \
        WHERE (_incident_records.dismissed IS NULL OR _incident_records.dismissed = FALSE) \
        AND (_incident_records.decision_id IS NULL OR _incident_records.decision_id != 0) \
        AND _incident_records.deleted_at IS NULL \
    "
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] == 15 or client_id == 15: req_str = req_str.replace("criticality_id", "priority_id").replace(" = 5", " = 1")

    req_params = [str(look_back_days)]
    
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] != 5:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(GLOBAL_INFOS['CURRENT_COMPANY_ID'])
    elif client_id is not None:
        req_str += "AND planifications.societe_id = %s "
        req_params.append(client_id)
    
    if country_id is not None:
        req_str += "AND sites.country_id = %s "
        req_params.append(country_id)
    
    req_str += "ORDER BY CAST(_inspected.date AS VARCHAR), _inspected.planification_id DESC "
    
    print("--- REQ", req_str, req_params)
    TW_DB_CURSOR.execute(req_str, req_params)
    
    severity_5_raw_data = []
    req_columns = [col[0] for col in TW_DB_CURSOR.description]
    for row in TW_DB_CURSOR:
        record = dict(zip(req_columns, row))
        severity_5_raw_data.append(record)
    
    return severity_5_raw_data

def get_recent_damage_evolution(look_back_days: int = 10, country_id: int | None = None, client_id: int | None = None):
    req_str = " \
        SELECT ____.*, _turbines.name AS turbine, _sites.name AS site, _sites.country_id \
        FROM (SELECT __.planification_id, __.turbine_id, count_2026, __.date, __.societe_id, __.site_id, count_2025 \
            FROM (SELECT _incidents.*, CAST(_inspections_status.date AS VARCHAR), _schedule_inspections.societe_id, _schedule_inspections.site_id \
                FROM (SELECT planification_id, turbine_id, count(*) AS count_2026 FROM incident_records WHERE deleted_at IS NULL GROUP BY planification_id, turbine_id) _incidents \
                JOIN (SELECT planification_id, societe_id, site_id FROM schedule_inspections WHERE campaign = 2026 ORDER BY planification_id) _schedule_inspections \
                ON _schedule_inspections.planification_id = _incidents.planification_id \
                JOIN (SELECT planification_id, turbine_id, date FROM inspections_status WHERE status_id = 7 AND date > NOW() - INTERVAL %s DAY ORDER BY planification_id, turbine_id) _inspections_status \
                ON _inspections_status.planification_id = _incidents.planification_id AND _inspections_status.turbine_id = _incidents.turbine_id \
            ) __ \
            JOIN (SELECT _incidents.*, _inspections_status.date \
                FROM (SELECT planification_id, turbine_id, count(*) AS count_2025 FROM incident_records WHERE deleted_at IS NULL GROUP BY planification_id, turbine_id) _incidents \
                JOIN (SELECT planification_id FROM schedule_inspections WHERE campaign = 2025 ORDER BY planification_id) _schedule_inspections \
                ON _schedule_inspections.planification_id = _incidents.planification_id \
                JOIN (SELECT planification_id, turbine_id, date FROM inspections_status WHERE status_id = 7 ORDER BY planification_id, turbine_id ) _inspections_status \
                ON _inspections_status.planification_id = _incidents.planification_id AND _inspections_status.turbine_id = _incidents.turbine_id \
            ) ___ \
            ON ___.turbine_id = __.turbine_id \
        ) ____ \
        JOIN (SELECT * FROM turbines) _turbines ON _turbines.id = ____.turbine_id \
        JOIN (SELECT * FROM sites) _sites ON _sites.id = ____.site_id \
    "
    req_params = [str(look_back_days)]
    
    if GLOBAL_INFOS['CURRENT_COMPANY_ID'] != 5:
        req_str += "JOIN (SELECT id FROM planifications WHERE societe_id = %s) _planifications ON _planifications.id = ____.planification_id "
        req_params.append(GLOBAL_INFOS['CURRENT_COMPANY_ID'])
    elif client_id is not None:
        req_str += "JOIN (SELECT id FROM planifications WHERE societe_id = %s) _planifications ON _planifications.id = ____.planification_id "
        req_params.append(client_id)
    
    TW_DB_CURSOR.execute(req_str, req_params)
    
    damage_evolution_raw_data = []
    req_columns = [col[0] for col in TW_DB_CURSOR.description]
    for row in TW_DB_CURSOR:
        record = dict(zip(req_columns, row))
        
        if country_id is None or record['country_id'] == country_id:
            damage_evolution_raw_data.append(record)
    
    return damage_evolution_raw_data