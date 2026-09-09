"""
Évolution des dommages d'une éolienne entre deux campagnes d'inspection.

Deux analyses, deux logiques distinctes :

- Fissures (cracks) : chaque fissure a une identité physique, retrouvée d'une
  campagne à l'autre par sa position dans l'espace de la pale. On compare
  fissure par fissure, avec un seuil de croissance qui dépend de la taille.

- Érosion du bord d'attaque (LEE) : les zones érodées fusionnent et se scindent,
  elles n'ont pas d'identité stable. La référence est la longueur érodée totale
  de la pale, avec un seuil unique de 5%.

Deux modes d'appel côté érosion :

- une turbine : planification_id + turbine_id, comportement historique
- un site entier : planification_id + site_id, toutes les turbines du parc,
  chacune avec SA campagne courante et SA campagne précédente — un site
  inspecté en plusieurs lots reste couvert intégralement.

Fonctions publiques attendues par les @tool du serveur MCP :

    resolve_previous_planification(turbine_id, planification_id)
    resolve_site_turbine_ids(site_id)
    resolve_turbine_names(turbine_ids)
    resolve_current_by_turbine(turbine_ids, planification_id)
    resolve_previous_by_turbine(turbine_ids_or_map, planification_id=None)
    fetch_crack_data / fetch_erosion_data / fetch_erosion_data_bulk
    build_cracks_by_face / build_blade_erosion
    select_blade / select_turbine
    compare_blade_cracks / compare_blade_erosion
    compare_turbines
    BLADES, FACES
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field

import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

from maia_utils import TW_DB_CURSOR
from wind.wind_calculator import WINDCalculator

# =========================================================================== #
# Constantes communes
# =========================================================================== #
FACES = ["Pressure side", "Leading Edge", "Suction side", "Trailing Edge"]
FACE_RANKS = {face: rank for rank, face in enumerate(FACES)}
BLADES = ["A", "B", "C"]

# L'érosion ne concerne que le bord d'attaque et son débordement latéral.
LEE_FACES = ["Pressure side", "Leading Edge", "Suction side"]

# Classements servant à choisir la valeur dominante d'un groupe de dommages.
# Les niveaux "+" doivent y figurer : un index() sur une valeur absente lève.
WIND_RANKING = ["Error", "C1", "C2", "C2+", "C3", "M1", "M2", "M2+", "M3",
                "S1", "S2", "S2+", "S3"]
PART_RANKING = ["Coat", "Laminate", "Bonding line", "Tip end"]
SHAPE_RANKING = [None, "straight", "curved", "stripes", "multibranched"]

# WIND pour lesquels l'aggravation était prévue
WIND_GROWTH_PREDICTED = ["M2", "M3", "S2", "S3"]


# =========================================================================== #
# Constantes fissures
# =========================================================================== #
CRACK_DEFECT_TYPE = "Crack"

# Croissance significative : (taille précédente en mm) -> % minimum.
# Valeurs statistiques reprises de make_crack_size_sentence.
CRACK_GROWTH_THRESHOLDS = {
    (0, 30): 50.60,
    (30, 50): 32.40,
    (50, 100): 21.80,
    (100, 150): 19.10,
    (150, 250): 20.70,
    (250, math.inf): 11.90,
}

# Fissures mesurées en surface plutôt qu'en longueur. L'ancien code ne comparait
# pas ces cas : on remonte la taille courante sans delta.
AREA_SHAPES = ["multibranched", "stripes"]

# Distance maximale, en mètres, pour apparier deux fissures qui ne se chevauchent pas.
CRACK_MATCH_MAX_GAP = 0.5

# Fenêtre d'isolement : le repli par proximité n'est tenté que si la zone ne
# contient aucune autre fissure précédente à cette distance. Sinon, rien ne dit
# laquelle des deux est la bonne.
CRACK_MATCH_ISOLATION_RADIUS = 5.0


# =========================================================================== #
# Constantes érosion
# =========================================================================== #
LEE_DEFECT_TYPE = "Leading Edge Erosion"

# Variation minimale pour être significative, en %.
LEE_GROWTH_THRESHOLD_PCT = 5.0

# Recouvrement minimal, en fraction de la zone, pour apparier deux zones érodées.
LEE_SECTION_MATCH_MIN_OVERLAP = 0.25


# =========================================================================== #
# Accès base
# =========================================================================== #
# TW_DB_CURSOR vient de maia_utils : c'est un curseur psycopg2 classique, il
# renvoie des tuples et non des dictionnaires. D'où cursor_to_dataframe plus bas.
WW_DB_PARAMS = dict(dbname="windwatch", user="singulair", password="singulair",
                    host="localhost")


# Campagne d'inspection précédant celle donnée, pour une turbine.
# asset_scope garantit que la turbine était bien dans le périmètre inspecté.
PLANIFICATION_HISTORY_QUERY = """
    SELECT _asset_scope.planification_id, date
    FROM (SELECT id, date FROM planifications
          JOIN controle_planification
            ON controle_planification.planification_id = planifications.id
          WHERE deleted_at IS NULL AND controle_planification.controle_id = 1) _planifications
    JOIN (SELECT planification_id, turbine_id FROM asset_scope WHERE deleted_at IS NULL) _asset_scope
      ON _asset_scope.planification_id = _planifications.id
    JOIN (SELECT turbine_id, planification_id, count(*) FROM incident_records
          WHERE deleted_at IS NULL GROUP BY planification_id, turbine_id) _incident_records
      ON _incident_records.planification_id = _asset_scope.planification_id
     AND _incident_records.turbine_id = _asset_scope.turbine_id
    WHERE _asset_scope.turbine_id = %s
      AND date < (SELECT date FROM planifications WHERE id = %s)
    ORDER BY date DESC
    LIMIT 1
"""

# Même requête, borne incluse : la campagne "courante" d'une turbine, c'est-à-dire
# la plus récente à la date de la planification demandée ou avant. Une turbine
# inspectée dans un autre lot du même site a ainsi sa propre campagne.
CURRENT_PLANIFICATION_QUERY = PLANIFICATION_HISTORY_QUERY.replace(
    "AND date < (SELECT date FROM planifications WHERE id = %s)",
    "AND date <= (SELECT date FROM planifications WHERE id = %s)",
)


# Les jointures INNER sont conservées même quand leur colonne n'est pas
# sélectionnée : elles filtrent les lignes, les retirer changerait le résultat.
# Le filtre sur le type de défaut est fait en SQL, pas en pandas.
CRACK_QUERY = """
    SELECT
        _incidents.id AS damage_id,
        _incidents.turbine_id AS turbine_id,
        components_turbines.name AS blade,
        parts.label_en AS side,
        FLOOR(radius) AS radius,
        ((radius_img - (((("Sensor_resolution_height" / 2)
          - (floor(((xy_coordinates->0->0)->1)::jsonb::NUMERIC) + 20))
          * conv_pixelh_m) / 1000))
        + (radius_img - (((("Sensor_resolution_height" / 2)
          - (floor(((xy_coordinates->0->2)->1)::jsonb::NUMERIC) + 20))
          * conv_pixelh_m) / 1000))) / 2.0 AS radius_float,
        components.label_en AS part_damaged,
        CASE WHEN criticality_id = 6 THEN 0 ELSE criticality_id END AS criticality,
        damage_axes.name AS crack_axis,
        damage_shapes.name AS crack_shape,
        wind, wind_severity, wind_desc, wind_new_desc, wind_new_sev,
        xy_coordinates, conv_pixelh_m, conv_pixelw_m,
        models.name AS model,
        models.blade_length AS blade_length,
        turbines.tower_height AS tower_height,
        turbines.altitude, turbines.distance_to_sea,
        turbines.latitude, turbines.longitude,
        turbines.soil_texture_id, turbines.temperatures,
        "Sensor_resolution_height" AS image_height,
        turbines.wind_speed,
        dp_start, dp_end, radius_img
    FROM (
        SELECT * FROM incident_records
        WHERE dismissed = FALSE AND decision_id != 0 AND deleted_at IS NULL
        AND planification_id = %s AND turbine_id = %s
    ) _incidents
    JOIN components ON components.id = _incidents.component_id
    JOIN defect_types ON defect_types.id = _incidents.defect_type_id
    JOIN analysis ON analysis.id = _incidents.analysi_id
    JOIN parts ON parts.id = _incidents.part_id
    LEFT JOIN crack_infos ON crack_infos.damage_id = _incidents.id
    LEFT JOIN damage_axes ON damage_axes.id = damage_axis_id
    LEFT JOIN damage_shapes ON damage_shapes.id = damage_shape_id
    JOIN components_turbines ON components_turbines.id = _incidents.component_turbine_id
    JOIN turbines ON turbines.id = _incidents.turbine_id
    JOIN models ON models.id = turbines.model_name
    WHERE conv_pixelh_m IS NOT NULL AND conv_pixelw_m IS NOT NULL
      AND defect_types.label_en = %s
      AND components.label_en != 'Drain hole'
"""

# L'érosion n'a pas besoin des colonnes de position spatiale : elle s'apparie
# par plage de rayons, pas par bounding box.
# turbine_id est sélectionné : select_turbine en a besoin pour découper le
# DataFrame quand on charge plusieurs turbines d'un coup.
EROSION_QUERY = """
    SELECT
        _incidents.turbine_id AS turbine_id,
        components_turbines.name AS blade,
        parts.label_en AS side,
        FLOOR(radius) AS radius,
        components.label_en AS part_damaged,
        CASE WHEN criticality_id = 6 THEN 0 ELSE criticality_id END AS criticality,
        wind, wind_severity, wind_desc, wind_new_desc, wind_new_sev,
        xy_coordinates, conv_pixelh_m, conv_pixelw_m,
        models.name AS model,
        models.blade_length AS blade_length,
        turbines.tower_height AS tower_height,
        turbines.altitude, turbines.distance_to_sea,
        turbines.latitude, turbines.longitude,
        turbines.soil_texture_id, turbines.temperatures,
        turbines.wind_speed
    FROM (
        SELECT * FROM incident_records
        WHERE dismissed = FALSE AND decision_id != 0 AND deleted_at IS NULL
        AND planification_id = %s AND turbine_id = %s
    ) _incidents
    JOIN components ON components.id = _incidents.component_id
    JOIN defect_types ON defect_types.id = _incidents.defect_type_id
    JOIN analysis ON analysis.id = _incidents.analysi_id
    JOIN parts ON parts.id = _incidents.part_id
    JOIN components_turbines ON components_turbines.id = _incidents.component_turbine_id
    JOIN turbines ON turbines.id = _incidents.turbine_id
    JOIN models ON models.id = turbines.model_name
    WHERE conv_pixelh_m IS NOT NULL AND conv_pixelw_m IS NOT NULL
      AND defect_types.label_en = %s
"""

# Même requête, mais sur un lot de turbines : une seule requête au lieu de N.
EROSION_QUERY_BULK = EROSION_QUERY.replace(
    "AND planification_id = %s AND turbine_id = %s",
    "AND planification_id = %s AND turbine_id = ANY(%s)",
)


def cursor_to_dataframe(cursor):
    """Curseur psycopg2 classique -> DataFrame avec les vrais noms de colonnes.

    Sans le paramètre columns, pandas numéroterait les colonnes 0, 1, 2... et
    tous les accès par nom du reste du fichier échoueraient.
    """
    rows = cursor.fetchall()
    columns = [column[0] for column in cursor.description]
    return pd.DataFrame(rows, columns=columns)


def fetch_crack_data(planification_id, turbine_id):
    TW_DB_CURSOR.execute(CRACK_QUERY, (planification_id, turbine_id, CRACK_DEFECT_TYPE))
    return cursor_to_dataframe(TW_DB_CURSOR)


def fetch_erosion_data(planification_id, turbine_id):
    TW_DB_CURSOR.execute(EROSION_QUERY, (planification_id, turbine_id, LEE_DEFECT_TYPE))
    return cursor_to_dataframe(TW_DB_CURSOR)


def fetch_erosion_data_bulk(planification_id, turbine_ids):
    """Dommages d'érosion de plusieurs turbines, en une seule requête."""
    if not turbine_ids:
        return pd.DataFrame()

    TW_DB_CURSOR.execute(
        EROSION_QUERY_BULK,
        (planification_id, list(turbine_ids), LEE_DEFECT_TYPE),
    )
    return cursor_to_dataframe(TW_DB_CURSOR)


def resolve_previous_planification(turbine_id, planification_id):
    """Campagne d'inspection précédant celle donnée, ou None s'il n'y en a pas."""
    TW_DB_CURSOR.execute(PLANIFICATION_HISTORY_QUERY, (turbine_id, planification_id))
    row = TW_DB_CURSOR.fetchone()
    return row[0] if row else None  # planification_id, première colonne du SELECT


def resolve_site_turbine_ids(site_id):
    """TOUTES les turbines du site, indépendamment des campagnes.

    On ne part pas d'asset_scope : une turbine peut avoir été inspectée dans un
    autre lot que la planification demandée, et elle serait alors invisible.
    """
    TW_DB_CURSOR.execute(
        "SELECT id FROM turbines "
        "WHERE site_id = %s AND deleted_at IS NULL "
        "ORDER BY name",
        (site_id,),
    )
    return [row[0] for row in TW_DB_CURSOR.fetchall()]


def resolve_turbine_names(turbine_ids):
    """{turbine_id: nom} en une requête.

    Le nom ne vient pas des requêtes de dommages : components_turbines.name y
    désigne la pale, pas la turbine.
    """
    if not turbine_ids:
        return {}

    TW_DB_CURSOR.execute(
        "SELECT id, name FROM turbines WHERE id = ANY(%s)",
        (list(turbine_ids),),
    )
    return {row[0]: row[1] for row in TW_DB_CURSOR.fetchall()}


def resolve_current_by_turbine(turbine_ids, planification_id):
    """{turbine_id: sa campagne courante} — celle du lot où elle a été inspectée.

    Pour une turbine inspectée dans la planification demandée, c'est elle-même.
    Pour une turbine d'un autre lot du même site, c'est sa propre campagne.
    Une turbine absente du dictionnaire n'a jamais été inspectée à cette date.
    """
    current_by_turbine = {}
    for turbine_id in turbine_ids:
        TW_DB_CURSOR.execute(CURRENT_PLANIFICATION_QUERY, (turbine_id, planification_id))
        row = TW_DB_CURSOR.fetchone()
        if row:
            current_by_turbine[turbine_id] = row[0]
    return current_by_turbine


def resolve_previous_by_turbine(turbine_ids_or_map, planification_id=None):
    """{turbine_id: planification précédente}.

    Accepte soit une liste de turbines avec une planification commune, soit le
    dictionnaire {turbine_id: sa campagne courante} — chaque turbine est alors
    comparée à ce qui précède SA campagne, pas celle du lot voisin.
    """
    if isinstance(turbine_ids_or_map, dict):
        pairs = list(turbine_ids_or_map.items())
    else:
        pairs = [(turbine_id, planification_id) for turbine_id in turbine_ids_or_map]

    previous_by_turbine = {}
    for turbine_id, current_id in pairs:
        previous = resolve_previous_planification(turbine_id, current_id)
        if previous is not None:
            previous_by_turbine[turbine_id] = previous
    return previous_by_turbine


# =========================================================================== #
# Préparation commune
# =========================================================================== #
def sort_data_by_face_and_radius(faces, radius, data_lists):
    """Tri en place par face (ordre FACE_RANKS) puis par rayon croissant."""
    for dmg_i in range(len(faces)):
        min_dmg_i = dmg_i
        for following_dmg_i in range(dmg_i + 1, len(faces)):
            if FACE_RANKS[faces[following_dmg_i]] < FACE_RANKS[faces[min_dmg_i]]:
                min_dmg_i = following_dmg_i
            elif (faces[following_dmg_i] == faces[min_dmg_i]
                  and radius[following_dmg_i] < radius[min_dmg_i]):
                min_dmg_i = following_dmg_i

        faces[dmg_i], faces[min_dmg_i] = faces[min_dmg_i], faces[dmg_i]
        radius[dmg_i], radius[min_dmg_i] = radius[min_dmg_i], radius[dmg_i]
        for data in data_lists:
            data[dmg_i], data[min_dmg_i] = data[min_dmg_i], data[dmg_i]


def get_dmg_size(row):
    """Dimensions physiques du dommage, en mm, depuis la bbox pixel."""
    rectangle = json.loads(str(row["xy_coordinates"]))[0]
    x1, _ = rectangle[0]
    x2, y2 = rectangle[1]
    _, y3 = rectangle[2]

    return pd.Series(
        [abs(x2 - x1) * float(row["conv_pixelw_m"]),
         abs(y3 - y2) * float(row["conv_pixelh_m"])],
        index=["width", "height"],
    )


def add_wind_and_size(blade_data):
    """Ajoute l'index WIND et les dimensions physiques à un DataFrame de pale."""
    blade_data = blade_data.copy()
    wind_calculator = WINDCalculator()
    blade_data.loc[:, ("wind_val",)] = blade_data.apply(wind_calculator.getDmgWIND, axis=1)
    blade_data.loc[:, ("width", "height")] = blade_data.apply(get_dmg_size, axis=1)
    return blade_data


def select_blade(full_data, blade):
    """Lignes d'une pale, ou DataFrame vide."""
    if full_data.empty:
        return pd.DataFrame()
    return full_data.loc[full_data["blade"] == blade]


def select_turbine(full_data, turbine_id):
    """Lignes d'une turbine dans un DataFrame multi-turbines."""
    if full_data.empty:
        return pd.DataFrame()
    return full_data.loc[full_data["turbine_id"] == turbine_id]


# =========================================================================== #
# FISSURES — regroupement
# =========================================================================== #
def boxes_overlap(box1, box2):
    """Chevauchement de deux boîtes [x_min, x_max, y_min, y_max]."""
    if box1 is None or box2 is None:
        return False
    try:
        box1 = [float(i) for i in box1]
        box2 = [float(i) for i in box2]
    except (ValueError, TypeError):
        return False
    if len(box1) != 4 or len(box2) != 4:
        return False

    x1_min, x1_max, y1_min, y1_max = box1
    x2_min, x2_max, y2_min, y2_max = box2

    # Comparaisons non strictes en X : sur le bord de fuite, dp_start == dp_end,
    # la boîte est plate et un test strict rejetterait tout.
    if x1_min > x2_max or x2_min > x1_max:
        return False
    if y1_min >= y2_max or y2_min >= y1_max:
        return False
    return True


def get_bounding_box_in_space(row):
    """Position du dommage dans l'espace de la pale : c'est elle qui permet l'appariement.

    radius_float est calculé en SQL : c'est le centre du dommage. On redescend
    d'une demi-hauteur pour obtenir la borne basse de l'intervalle.
    """
    try:
        if (pd.isna(row["radius_float"]) or not row["blade_length"]
                or not row["dp_start"] or not row["dp_end"] or not row["height"]):
            return pd.Series([None], index=["bbox_espace"])

        height_m = float(row["height"]) / 1000
        bas = float(row["radius_float"]) - height_m / 2
        haut = min(bas + height_m, float(row["blade_length"]))

        return pd.Series([[
            float(row["dp_start"]),
            float(row["dp_end"]),
            bas,
            haut,
        ]], index=["bbox_espace"])

    except Exception as exc:
        print(f"bbox impossible pour damage_id={row.get('damage_id', 'inconnu')} : {exc}")
        return pd.Series([None], index=["bbox_espace"])


def group_cracks_by_face(crack_raw_data):
    """
    Classe les fissures par face et par rayon.

    Les fissures verticales dont les rayons se chevauchent sont fusionnées en une
    section unique : on cumule la hauteur et on garde les valeurs dominantes
    (WIND, partie endommagée, sévérité, largeur, forme) ainsi qu'un id
    représentatif — celui de la fissure la plus grave de la section.
    """
    if crack_raw_data is None or crack_raw_data.empty:
        return None

    ids = list(crack_raw_data["damage_id"])
    radius = list(crack_raw_data["radius"])
    heights = list(crack_raw_data["height"])
    widths = list(crack_raw_data["width"])
    winds = list(crack_raw_data["wind_val"])
    parts = list(crack_raw_data["part_damaged"])
    sevs = list(crack_raw_data["criticality"])
    faces = list(crack_raw_data["side"])
    axis = list(crack_raw_data["crack_axis"])
    shapes = list(crack_raw_data["crack_shape"])
    bboxes = list(crack_raw_data["bbox_espace"])

    sort_data_by_face_and_radius(
        faces, radius, [ids, heights, widths, winds, parts, sevs, axis, shapes, bboxes])

    cracks = []
    section = None
    last_radius_end = -1.0

    for (dmg_id, dmg_radius, dmg_height, dmg_width, dmg_wind, dmg_part,
         dmg_sev, dmg_face, dmg_axis, dmg_shape, dmg_bbox) in zip(
            ids, radius, heights, widths, winds, parts, sevs, faces, axis, shapes, bboxes):

        if dmg_wind is None:
            dmg_wind = "Error"

        # mm -> m
        dmg_height = dmg_height / 1000.0
        dmg_width = dmg_width / 1000.0
        dmg_radius_start = float(dmg_radius) + 0.5 - dmg_height / 2
        dmg_radius_end = float(dmg_radius) + 0.5 + dmg_height / 2

        # Fissure non verticale : conservée telle quelle, jamais fusionnée
        if dmg_axis != "vertical":
            cracks.append({
                "id": dmg_id, "radius": float(dmg_radius),
                "height": dmg_height, "width": dmg_width,
                "orientation": dmg_axis, "wind": dmg_wind, "part": dmg_part,
                "sev": dmg_sev, "face": dmg_face, "shape": dmg_shape,
                "bbox_espace": dmg_bbox,
            })
            continue

        same_section = (section is not None
                        and dmg_face == section["face"]
                        and dmg_radius_start <= last_radius_end)

        if same_section:
            section["height"] += dmg_height - (last_radius_end - dmg_radius_start)

            if WIND_RANKING.index(dmg_wind) > WIND_RANKING.index(section["wind"]):
                section["wind"] = dmg_wind
                section["id"] = dmg_id
            if PART_RANKING.index(dmg_part) > PART_RANKING.index(section["part"]):
                section["part"] = dmg_part
                section["id"] = dmg_id
            if dmg_sev > section["sev"]:
                section["sev"] = dmg_sev
                section["id"] = dmg_id
            if dmg_width > section["width"]:
                section["width"] = dmg_width
            if SHAPE_RANKING.index(dmg_shape) > SHAPE_RANKING.index(section["shape"]):
                section["shape"] = dmg_shape
        else:
            if section is not None:
                cracks.append(section)

            section = {
                "id": dmg_id, "radius": dmg_radius_start,
                "height": dmg_height, "width": dmg_width,
                "orientation": "vertical", "wind": dmg_wind, "part": dmg_part,
                "sev": dmg_sev, "face": dmg_face, "shape": dmg_shape,
                "bbox_espace": dmg_bbox,
            }

        last_radius_end = dmg_radius_end

    if section is not None:
        cracks.append(section)

    cracks_by_face = {face: [] for face in FACES}
    for crack in cracks:
        if crack["id"] not in [existing["id"] for existing in cracks_by_face[crack["face"]]]:
            cracks_by_face[crack["face"]].append(crack)

    return cracks_by_face


def enrich_cracks_with_measures(cracks_by_face):
    """Ajoute la mesure saisie manuellement (base windwatch), convertie en mètres."""
    if cracks_by_face is None:
        return None

    all_cracks = [crack for face_cracks in cracks_by_face.values() for crack in face_cracks]
    if not all_cracks:
        return cracks_by_face

    crack_ids = [crack["id"] for crack in all_cracks]

    connection = psycopg2.connect(**WW_DB_PARAMS)
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                "SELECT id, measure FROM incident_records WHERE id = ANY(%s)",
                (crack_ids,),
            )
            measures = {row["id"]: row["measure"] for row in cursor.fetchall()}
    finally:
        connection.close()

    for crack in all_cracks:
        raw = measures.get(crack["id"])
        if raw is None:
            crack["measure"] = None
            continue
        try:
            crack["measure"] = float(str(raw).replace(" ", "").replace(",", ".")) / 1000
        except ValueError:
            crack["measure"] = None

    return cracks_by_face


def build_cracks_by_face(blade_data):
    """DataFrame d'une pale -> fissures classées par face, mesures incluses."""
    if blade_data.empty:
        return None

    cracks = add_wind_and_size(blade_data)
    cracks = cracks.join(cracks.apply(get_bounding_box_in_space, axis=1))

    return enrich_cracks_with_measures(group_cracks_by_face(cracks))


def _vertical_gap(box1, box2):
    """Écart en mètres entre deux intervalles verticaux. 0 s'ils se chevauchent."""
    return max(0.0, max(box1[2], box2[2]) - min(box1[3], box2[3]))


def find_matching_previous_crack(crack, previous_cracks):
    """Apparie une fissure à celle de la campagne précédente.

    Passe 1 : chevauchement géométrique franc.
    Passe 2 : proximité verticale, mais uniquement si la fissure est isolée —
    aucune autre fissure précédente dans un rayon de CRACK_MATCH_ISOLATION_RADIUS
    mètres. En zone dense, on préfère ne rien apparier qu'apparier au hasard.
    """
    if not previous_cracks or crack.get("bbox_espace") is None:
        return None

    candidates = [previous for previous in previous_cracks
                  if previous.get("bbox_espace") is not None]

    # Passe 1 : chevauchement
    for previous in candidates:
        if boxes_overlap(crack["bbox_espace"], previous["bbox_espace"]):
            return previous

    # Passe 2 : proximité, si et seulement si la zone est isolée
    nearby = [previous for previous in candidates
              if _vertical_gap(crack["bbox_espace"], previous["bbox_espace"])
              <= CRACK_MATCH_ISOLATION_RADIUS]

    if len(nearby) != 1:
        return None

    if _vertical_gap(crack["bbox_espace"], nearby[0]["bbox_espace"]) <= CRACK_MATCH_MAX_GAP:
        return nearby[0]

    return None


# =========================================================================== #
# FISSURES — comparaison
# =========================================================================== #
def get_crack_growth_threshold(size_m):
    """% de croissance minimum pour être significative, selon la taille précédente."""
    size_mm = size_m * 1000
    for (low, high), value in CRACK_GROWTH_THRESHOLDS.items():
        if low <= size_mm < high:
            return value
    return None


def get_crack_measurement(crack):
    """(valeur, unité) : surface en m2 pour les formes étendues, longueur en m sinon."""
    if crack.get("shape") in AREA_SHAPES:
        area = crack.get("measure")
        if area is None:
            area = crack["width"] * crack["height"]
        return area, "area"

    length = crack.get("measure")
    if length is None:
        if crack["orientation"] == "horizontal":
            length = crack["width"]
        elif crack["orientation"] == "vertical":
            length = crack["height"]
        else:
            length = math.sqrt(crack["height"] ** 2 + crack["width"] ** 2)
    return length, "length"


def measure_source(damage):
    """db = mesure saisie, image = mesure calculée depuis la photo."""
    return "db" if damage.get("measure") is not None else "image"


@dataclass
class CrackEvolution:
    damage_id: int
    previous_damage_id: int | None
    blade: str
    face: str
    # new / grown / stable / repaired — jamais "shrunk" : une fissure ne
    # rétrécit pas, une baisse de mesure est classée "stable".
    status: str
    orientation: str | None = None
    shape: str | None = None
    part: str | None = None
    radius: float | None = None

    measured_as: str = "length"       # length (m) ou area (m2)
    size: float | None = None
    previous_size: float | None = None
    size_delta: float | None = None
    growth_percentage: float | None = None
    growth_threshold: float | None = None
    significant: bool | None = None

    severity: int | None = None
    previous_severity: int | None = None
    wind: str | None = None
    previous_wind: str | None = None
    growth_was_predicted: bool = False

    # Un delta entre deux sources différentes n'est pas comparable.
    measure_source: str | None = None
    previous_measure_source: str | None = None


def compare_blade_cracks(cracks_by_face, previous_cracks_by_face, blade):
    """Apparie les fissures d'une pale entre deux campagnes et calcule leur évolution."""
    evolutions = []

    for face in FACES:
        current = (cracks_by_face or {}).get(face, [])
        previous = (previous_cracks_by_face or {}).get(face, [])
        matched_previous_ids = set()

        for crack in current:
            match = find_matching_previous_crack(crack, previous)
            if match is not None:
                matched_previous_ids.add(match["id"])

            size, unit = get_crack_measurement(crack)

            evolution = CrackEvolution(
                damage_id=crack["id"],
                previous_damage_id=match["id"] if match else None,
                blade=blade,
                face=face,
                status="new" if match is None else "stable",
                orientation=crack.get("orientation"),
                shape=crack.get("shape"),
                part=crack.get("part"),
                radius=crack.get("radius"),
                measured_as=unit,
                size=size,
                severity=crack.get("sev"),
                wind=crack.get("wind"),
                measure_source=measure_source(crack),
            )

            if match is not None:
                previous_size, _ = get_crack_measurement(match)

                evolution.previous_size = previous_size
                evolution.previous_severity = match.get("sev")
                evolution.previous_wind = match.get("wind")
                evolution.previous_measure_source = measure_source(match)

                # Les surfaces ne sont pas comparées : l'ancien code ne le faisait pas
                if unit == "length":
                    delta = size - round(previous_size, 2)
                    evolution.size_delta = delta
                    evolution.growth_percentage = (
                        0.0 if previous_size == 0 else (abs(delta) / previous_size) * 100)
                    evolution.growth_threshold = get_crack_growth_threshold(previous_size)
                    evolution.significant = (
                        evolution.growth_threshold is not None
                        and evolution.growth_percentage >= evolution.growth_threshold)

                    # Une fissure ne peut pas rétrécir : un delta négatif est un
                    # écart de mesure, pas une évolution.
                    if evolution.significant and delta <= 0:
                        evolution.significant = False

                    if evolution.significant:
                        evolution.status = "grown"
                        if match.get("wind") in WIND_GROWTH_PREDICTED:
                            evolution.growth_was_predicted = True

            evolutions.append(evolution)

        # Fissures présentes avant, absentes maintenant
        for previous_crack in previous:
            if previous_crack["id"] in matched_previous_ids:
                continue
            previous_size, unit = get_crack_measurement(previous_crack)
            evolutions.append(CrackEvolution(
                damage_id=previous_crack["id"],
                previous_damage_id=previous_crack["id"],
                blade=blade,
                face=face,
                status="repaired",
                orientation=previous_crack.get("orientation"),
                shape=previous_crack.get("shape"),
                part=previous_crack.get("part"),
                radius=previous_crack.get("radius"),
                measured_as=unit,
                previous_size=previous_size,
                previous_severity=previous_crack.get("sev"),
                previous_wind=previous_crack.get("wind"),
                previous_measure_source=measure_source(previous_crack),
            ))

    # Les fissures dont la mesure a baissé ne sont pas remontées du tout.
    return [e for e in evolutions if not (e.size_delta is not None and e.size_delta < 0)]


# =========================================================================== #
# ÉROSION — regroupement
# =========================================================================== #
def empty_erosion():
    """(zones, longueur totale, longueur laminate, zones intrados, zones extrados)."""
    return [], 0.0, 0.0, 0, 0


def build_lee_sections(lee_raw_data):
    """
    Regroupe les dommages d'érosion en zones continues.

    Une zone est une plage de rayons sur une même face où les dommages se
    chevauchent. On retient sa longueur érodée réelle, sa sévérité maximale, le
    WIND le plus élevé, et la partie la plus profondément atteinte.
    Toutes les sévérités sont prises en compte.
    """
    if lee_raw_data is None or lee_raw_data.empty:
        return empty_erosion()

    radius = list(lee_raw_data["radius"])
    faces = list(lee_raw_data["side"])
    heights = list(lee_raw_data["height"])
    winds = list(lee_raw_data["wind_val"])
    parts = list(lee_raw_data["part_damaged"])
    sevs = list(lee_raw_data["criticality"])

    sort_data_by_face_and_radius(faces, radius, [heights, winds, parts, sevs])

    total_lee = 0.0
    total_laminate = 0.0
    sections = []
    section = None
    last_radius_end = -1.0

    for dmg_radius, dmg_face, dmg_height, dmg_wind, dmg_part, dmg_sev in zip(
            radius, faces, heights, winds, parts, sevs):

        if dmg_face not in LEE_FACES or dmg_height is None:
            continue
        if dmg_wind is None:
            dmg_wind = "Error"

        dmg_height = dmg_height / 1000.0
        dmg_radius_start = float(dmg_radius) + 0.5 - dmg_height / 2
        dmg_radius_end = float(dmg_radius) + 0.5 + dmg_height / 2

        same_section = (section is not None
                        and dmg_face == section["face"]
                        and dmg_radius_start <= last_radius_end)

        # Seule la portion non déjà couverte compte : sans ça, deux dommages qui
        # se chevauchent gonfleraient artificiellement la longueur érodée.
        if same_section:
            added = dmg_height - (min(dmg_radius_end, last_radius_end) - dmg_radius_start)
        else:
            added = dmg_height

        if dmg_face == "Leading Edge":
            total_lee += added
        if dmg_part == "Laminate":
            total_laminate += added

        if same_section:
            section["height"] += added
            section["end"] = dmg_radius_end

            if WIND_RANKING.index(dmg_wind) > WIND_RANKING.index(section["wind"]):
                section["wind"] = dmg_wind
            if dmg_sev > section["max_sev"]:
                section["max_sev"] = dmg_sev
                section["deepest_part"] = dmg_part
        else:
            if section is not None:
                sections.append(section)

            section = {"start": dmg_radius_start, "end": dmg_radius_end,
                       "face": dmg_face, "height": dmg_height, "wind": dmg_wind,
                       "max_sev": dmg_sev, "deepest_part": dmg_part}

        last_radius_end = dmg_radius_end

    if section is not None:
        sections.append(section)

    by_face = {face: [] for face in LEE_FACES}
    for item in sections:
        by_face[item["face"]].append(item)

    compute_lateral_spread(by_face)

    return (by_face["Leading Edge"], total_lee, total_laminate,
            len(by_face["Pressure side"]), len(by_face["Suction side"]))


def compute_lateral_spread(by_face):
    """
    Pour chaque zone du bord d'attaque, quelle fraction déborde sur les flancs.

    C'est le signal que l'érosion ne progresse plus seulement en longueur mais
    aussi en largeur, ce qui change la nature de la réparation.
    """
    for le_section in by_face["Leading Edge"]:
        le_size = le_section["end"] - le_section["start"]

        for side in ["Pressure side", "Suction side"]:
            le_section[side] = 0.0
            if le_size <= 0:
                continue
            for side_section in by_face[side]:
                overlap = (min(side_section["end"], le_section["end"])
                           - max(side_section["start"], le_section["start"]))
                if overlap > 0:
                    le_section[side] += overlap / le_size


def build_blade_erosion(blade_data):
    """DataFrame d'une pale -> zones érodées et totaux."""
    if blade_data.empty:
        return empty_erosion()
    return build_lee_sections(add_wind_and_size(blade_data))


# =========================================================================== #
# ÉROSION — comparaison
# =========================================================================== #
def find_matching_previous_section(section, previous_sections):
    """Apparie deux zones érodées par recouvrement de leur plage de rayons."""
    size = section["end"] - section["start"]
    if size <= 0:
        return None

    best = None
    best_overlap = 0.0

    for previous in previous_sections:
        overlap = (min(previous["end"], section["end"])
                   - max(previous["start"], section["start"]))
        if overlap > best_overlap:
            best_overlap = overlap
            best = previous

    if best is None or best_overlap / size < LEE_SECTION_MATCH_MIN_OVERLAP:
        return None
    return best


def classify_erosion_change(size, previous_size):
    """(status, delta, pourcentage) selon le seuil de 5%.

    Une érosion ne régresse pas : un delta négatif est un écart de mesure,
    pas une évolution, et la zone reste "stable".
    """
    delta = size - previous_size
    percentage = 0.0 if previous_size == 0 else (abs(delta) / previous_size) * 100

    if percentage < LEE_GROWTH_THRESHOLD_PCT or delta <= 0:
        return "stable", delta, percentage
    return "grown", delta, percentage


@dataclass
class ErodedAreaEvolution:
    """Une zone érodée du bord d'attaque, comparée à la campagne précédente."""

    blade: str
    radius_start: float
    radius_end: float
    status: str                        # new / grown / stable / repaired
    eroded_length: float | None = None
    previous_eroded_length: float | None = None
    length_delta: float | None = None
    growth_percentage: float | None = None

    deepest_part: str | None = None    # Coat / Laminate / LE Tape
    max_severity: int | None = None
    previous_max_severity: int | None = None
    wind: str | None = None
    previous_wind: str | None = None
    growth_was_predicted: bool = False

    spread_pressure_side: float | None = None
    spread_suction_side: float | None = None


@dataclass
class BladeErosionEvolution:
    """Bilan d'une pale : c'est la longueur érodée totale qui fait référence."""

    blade: str
    status: str                    # new / grown / stable / repaired
    total_eroded_length: float
    previous_total_eroded_length: float 
    length_delta: float 
    growth_percentage: float 

    eroded_areas_count: int = 0
    previous_eroded_areas_count: int = 0 

    laminate_length: float = 0.0
    previous_laminate_length: float = 0.0 

    spreads_to_pressure_side: bool = False
    spreads_to_suction_side: bool = False
    previously_spread_to_pressure_side: bool = False 
    previously_spread_to_suction_side: bool = False 

    eroded_areas: list = field(default_factory=list)

@dataclass
class BladeErosion:
    """Bilan d'une pale : c'est la longueur érodée totale qui fait référence."""
    blade: str
    total_eroded_length: float

    eroded_areas_count: int = 0

    laminate_length: float = 0.0

    spreads_to_pressure_side: bool = False
    spreads_to_suction_side: bool = False

    eroded_areas: list = field(default_factory=list)
    
@dataclass
class ErodedArea:
    """Une zone érodée du bord d'attaque, comparée à la campagne précédente."""

    blade: str
    radius_start: float
    radius_end: float
    status: str                        # new / grown / stable / repaired
    eroded_length: float | None = None

    deepest_part: str | None = None    # Coat / Laminate / LE Tape
    max_severity: int | None = None
    wind: str | None = None


    spread_pressure_side: float | None = None
    spread_suction_side: float | None = None

def compare_eroded_areas(sections, previous_sections, blade):
    """Détail zone par zone, apparié par recouvrement de rayons."""
    areas = []
    matched = set()

    for section in sections:
        match = find_matching_previous_section(section, previous_sections)
        if match is not None:
            matched.add(id(match))

        area = ErodedAreaEvolution(
            blade=blade,
            radius_start=section["start"],
            radius_end=section["end"],
            status="new" if match is None else "stable",
            eroded_length=section["height"],
            deepest_part=section["deepest_part"],
            max_severity=section["max_sev"],
            wind=section["wind"],
            spread_pressure_side=section.get("Pressure side"),
            spread_suction_side=section.get("Suction side"),
        )

        if match is not None:
            status, delta, percentage = classify_erosion_change(
                section["height"], match["height"])

            area.status = status
            area.previous_eroded_length = match["height"]
            area.length_delta = delta
            area.growth_percentage = percentage
            area.previous_max_severity = match["max_sev"]
            area.previous_wind = match["wind"]

            if status == "grown" and match["wind"] in WIND_GROWTH_PREDICTED:
                area.growth_was_predicted = True

        areas.append(area)

    for previous_section in previous_sections:
        if id(previous_section) in matched:
            continue
        areas.append(ErodedAreaEvolution(
            blade=blade,
            radius_start=previous_section["start"],
            radius_end=previous_section["end"],
            status="repaired",
            previous_eroded_length=previous_section["height"],
            previous_max_severity=previous_section["max_sev"],
            previous_wind=previous_section["wind"],
        ))

    return areas


def compare_blade_erosion(current, previous, blade, include_areas=True):
    """Compare le bilan d'érosion d'une pale entre deux campagnes."""
    sections, total, laminate, nb_ps, nb_ss = current
    (previous_sections, previous_total, previous_laminate,
     previous_nb_ps, previous_nb_ss) = previous

    if total == 0 and previous_total == 0:
        return None

    if previous_total == 0:
        status, delta, percentage = "new", total, 100.0
    elif total == 0:
        status, delta, percentage = "repaired", -previous_total, 100.0
    else:
        status, delta, percentage = classify_erosion_change(total, previous_total)

    evolution = BladeErosionEvolution(
        blade=blade,
        status=status,
        total_eroded_length=total,
        previous_total_eroded_length=previous_total,
        length_delta=delta,
        growth_percentage=percentage,
        eroded_areas_count=len(sections),
        previous_eroded_areas_count=len(previous_sections),
        laminate_length=laminate,
        previous_laminate_length=previous_laminate,
        spreads_to_pressure_side=nb_ps > 0,
        spreads_to_suction_side=nb_ss > 0,
        previously_spread_to_pressure_side=previous_nb_ps > 0,
        previously_spread_to_suction_side=previous_nb_ss > 0,
    )

    if include_areas:
        evolution.eroded_areas = compare_eroded_areas(sections, previous_sections, blade)

    return evolution


def compute_eroded_areas(sections, blade):
    """Détail zone par zone, apparié par recouvrement de rayons."""
    areas = []
    for section in sections:

        area = ErodedArea(
            blade=blade,
            radius_start=section["start"],
            radius_end=section["end"],
            eroded_length=section["height"],
            deepest_part=section["deepest_part"],
            max_severity=section["max_sev"],
            wind=section["wind"],
            spread_pressure_side=section.get("Pressure side"),
            spread_suction_side=section.get("Suction side"),
        )


        areas.append(area)

    return areas

def compute_blade_erosion(current, blade, include_areas=True):
    """Compare le bilan d'érosion d'une pale entre deux campagnes."""
    sections, total, laminate, nb_ps, nb_ss = current

    if total == 0:
        return None

    evolution = BladeErosion(
        blade=blade,
        total_eroded_length=total,
        eroded_areas_count=len(sections),
        laminate_length=laminate,
        spreads_to_pressure_side=nb_ps > 0,
        spreads_to_suction_side=nb_ss > 0,
    )

    if include_areas:
        evolution.eroded_areas = compute_eroded_areas(sections, blade)

    return evolution


def compare_one_turbine(turbine_id, current_data, previous_data, blade, include_areas):
    """Compare les pales d'une turbine à partir de DataFrames déjà chargés."""
    evolutions = []
    for current_blade in ([blade] if blade else BLADES):
        evolution = compare_blade_erosion(
            build_blade_erosion(select_blade(current_data, current_blade)),
            build_blade_erosion(select_blade(previous_data, current_blade)),
            current_blade,
            include_areas,
        )
        if evolution is not None:
            evolutions.append(evolution)

    if not evolutions:
        return None
    return {"turbine_id": turbine_id, "blades": [asdict(e) for e in evolutions]}


def _turbine_frames(turbine_id, current_by_turbine, previous_by_turbine,
                    current_data_by_planification, previous_data_by_planification):
    """(données courantes, données précédentes) de cette turbine.
    OU données courantes de la turbine en fonction de l'appel de fonction"""
    current_id = current_by_turbine[turbine_id]
    if previous_by_turbine:
        previous_id = previous_by_turbine[turbine_id]
        return (
            select_turbine(current_data_by_planification[current_id], turbine_id),
            select_turbine(previous_data_by_planification[previous_id], turbine_id),
            current_id,
            previous_id,
        )
    else:
        return (
            select_turbine(current_data_by_planification[current_id], turbine_id),
            current_id,
        )


def compare_turbines(turbine_ids, current_by_turbine, previous_by_turbine,
                                current_data_by_planification,
                                previous_data_by_planification, blade, include_areas):
    """Comparaison turbine par turbine.

    Séquentiel volontairement : le SQL est déjà groupé, et le calcul restant est
    du Python pur — le GIL rend le multithread inutile ici.
    """
    payload = []
    for turbine_id in turbine_ids:
        current_data, previous_data, current_id, previous_id = _turbine_frames(
            turbine_id, current_by_turbine, previous_by_turbine,
            current_data_by_planification, previous_data_by_planification)

        result = compare_one_turbine(turbine_id, current_data, previous_data,
                                     blade, include_areas)
        if result is not None:
            result["planification_id"] = current_id
            result["previous_planification_id"] = previous_id
            payload.append(result)
    return payload

def describe_one_turbine(turbine_id, current_data, blade, include_areas):
    """État d'érosion des pales d'une turbine, sans comparaison.

    Pendant de compare_one_turbine : même structure de sortie, une seule campagne.
    """
    erosions = []
    for current_blade in ([blade] if blade else BLADES):
        erosion = compute_blade_erosion(
            build_blade_erosion(select_blade(current_data, current_blade)),
            current_blade,
            include_areas,
        )
        # None quand la pale n'a aucune érosion : elle n'apparaît pas.
        if erosion is not None:
            erosions.append(erosion)

    if not erosions:
        return None
    return {"turbine_id": turbine_id, "blades": [asdict(e) for e in erosions]}


def get_current_data_for_turbines(turbine_ids, current_by_turbine,
                                  current_data_by_planification,
                                  blade, include_areas):
    """État d'érosion courant de plusieurs turbines, sans comparaison."""
    payload = []
    for turbine_id in turbine_ids:
        current_data, current_id = _turbine_frames(
            turbine_id, current_by_turbine, None,
            current_data_by_planification, None)

        result = describe_one_turbine(turbine_id, current_data, blade, include_areas)
        if result is not None:
            result["planification_id"] = current_id
            payload.append(result)
    return payload



###The default one is for Leeding Edge Erosion for VESTAS and cracks fo ENERCON.