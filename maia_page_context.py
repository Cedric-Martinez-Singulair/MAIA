from maia_utils import TW_DB_CURSOR
from maia_utils import TurbineModel, Turbine, Planification
from maia_utils import TurbineDamageCountResult, PlanificationDamageCountResult

def get_current_page_context(site_id, planification_id):
    
    req_str = "SELECT incident_records.id, turbines.id, turbines.name, EXTRACT(YEAR FROM AGE(planifications.date, turbines.entry_service))::INT AS turbine_age, \
        models.id, models.name, components_turbines.name, parts.label_en, incident_records.radius, \
        components.label_en, defect_types.id, defect_types.label_en, analysis.label_en, criticalities.label_en \
    FROM incident_records  \
    JOIN planifications ON planifications.id = incident_records.planification_id \
    JOIN turbines ON turbines.id = incident_records.turbine_id \
    JOIN models ON models.id = turbines.model_name \
    JOIN components_turbines ON components_turbines.id = incident_records.component_turbine_id \
    JOIN parts ON parts.id = incident_records.part_id \
    JOIN components ON components.id = incident_records.component_id \
    JOIN defect_types ON defect_types.id = incident_records.defect_type_id \
    JOIN analysis ON incident_records.analysi_id = analysis.id \
    JOIN criticalities ON criticalities.id = incident_records.criticality_id \
    WHERE incident_records.deleted_at IS NULL AND planification_id = %s "
    
    TW_DB_CURSOR.execute(req_str, (planification_id,))
    raw_page_context = {}
    for row in TW_DB_CURSOR:
        damage_id, turbine_id, turbine_name, turbine_age, model_id, model_name, blade, face, radius, part_dmg, dmg_type_id, dmg_type, root_cause, criticality = row
        
        if turbine_id not in raw_page_context:
            raw_page_context[turbine_id] = {'turbine_infos': {'turbine_name': turbine_name, 'turbine_age': turbine_age, 'model_id': model_id, 'model_name': model_name}}
        if blade not in raw_page_context[turbine_id]:
            raw_page_context[turbine_id][blade] = {}
        if face not in raw_page_context[turbine_id][blade]:
            raw_page_context[turbine_id][blade][face] = []
        raw_page_context[turbine_id][blade][face].append({
            'damage_id': damage_id, 'radius': radius, 'part_damaged': part_dmg, 'damage_type_id': dmg_type_id, 'damage_type': dmg_type, 'root_cause': root_cause, 'criticality': criticality
        })
    
    formatted_page_context = {
        'webpage_type': 'inspection report',
        'webpage_content': {
            'site': {
                'site_id': 7196,
                'site_name': 'Moisson de Beauce',
                'site_country': {'country_id': 156, 'country_name': 'France'},
                'turbines': []
            },
            'planification': {
                'planification_id': 14630,
                'planification_date': '2026-03-31',
            }
        }
    }
    formatted_turbines = formatted_page_context['webpage_content']['site']['turbines']
    for turbine_id in raw_page_context:
        formatted_turbines.append({
            'turbine_id': turbine_id, 
            'turbine_name': raw_page_context[turbine_id]['turbine_infos']['turbine_name'], 
            'turbine_age': raw_page_context[turbine_id]['turbine_infos']['turbine_age'], 
            'turbine_model_id': raw_page_context[turbine_id]['turbine_infos']['model_id'], 
            'turbine_model_name': raw_page_context[turbine_id]['turbine_infos']['model_name'], 
            'blades': []
        })
        for blade in raw_page_context[turbine_id]:
            if blade == 'turbine_infos': continue
            formatted_turbines[-1]['blades'].append({
                'blade_name': blade, 'faces': []
            })
            for face in raw_page_context[turbine_id][blade]:
                formatted_turbines[-1]['blades'][-1]['faces'].append({
                    'face_name': face, 'damage': []
                })
                for damage in raw_page_context[turbine_id][blade][face]:
                    formatted_turbines[-1]['blades'][-1]['faces'][-1]['damage'].append(damage)
    
    print("CURRENT_PAGE_CONTEXT: " + str(formatted_page_context))
    return "CURRENT_PAGE_CONTEXT: " + str(formatted_page_context)

CURRENT_PAGE_CONTEXT = get_current_page_context(site_id=7196, planification_id=14630)