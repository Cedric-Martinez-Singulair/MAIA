"""aep_utils.py - version corrigée.

Chaque correctif est repéré dans le code par un commentaire `# PATCH #n`,
avec la même numérotation que le notebook AEP_loss_total_patched.ipynb.

Correctifs appliqués ici :
  #7  table de catégorisation unique (alignée sur le notebook) + combinaisons
      non couvertes journalisées au lieu de produire un None qui plantait
  #10 V_mean = V + 0.5 : la tranche de vent V = 26 m/s n'est plus perdue
  #11 boucle des tables intermédiaires jusqu'à V = 26
  #13 facteur de bout de pale : (TSRopt * B + 2) et non (TSRopt - B + 2)
  #14 P(V = 0) = 0 au lieu de NaN
  #15 CP_LER,plot utilise bien Ps_LER / CP_LER
  #23 get_turbine_damage : garde-fou sur dataframe vide, cl/cd-factor initialisé
  #24 create_intermediate_table : turbine_info() peut renvoyer None
  #25 le garde-fou NaN ne porte plus sur la totalité du dataframe
  #26 args=(i,) et filtres delete_damage / not_blades indépendants
  #27 Weibull / densité manquants détectés explicitement
  #28 cache borné et copie défensive

Optimisations de performance (aucune parallélisation) :
  PERF#1 create_intermediate_table accepte turbine_data / df_damage /
         df_V_data en paramètres optionnels, au lieu de les recalculer
         systématiquement (ces 3 valeurs ne dépendent pas de V)
  PERF#2 turbine_info_bis réutilise son propre df_output
  PERF#3 create_final_df calcule turbine_data/df_damage/df_final UNE
         fois et les réutilise pour les 26 vitesses (avant : jusqu'à
         ~26 rappels de turbine_info(), get_turbine_damage() et
         create_df() pour un résultat identique à chaque fois)
"""

from psycopg2.extras import RealDictCursor
import psycopg2

from psycopg2.pool import ThreadedConnectionPool

from maia_utils import TW_DB_CURSOR

import json
import numpy as np
import os
import pandas as pd
import math
from tqdm import tqdm
import time



## Catégorisation des dommages
# =============================================================================
# PATCH #7 - table de catégorisation unique et complète
#
# Cette table ne contenait que 6 entrées, alors que le notebook (Part 3) en
# utilisait 12 : les dommages "LE Paint" et "LE Laminate" ressortaient donc en
# None ici, et étaient traités comme sains alors qu'ils étaient comptés côté
# notebook. Les deux sources sont maintenant alignées.
#
# Deuxième point : une combinaison absente de la table donnait None, et None
# provoquait plus loin un TypeError (`None > 'a'`) dans get_turbine_damage(),
# ou disparaissait silencieusement selon le chemin d'appel. Le comportement
# est maintenant explicite et journalisé.
#
# ATTENTION - trous connus dans le tableau BAK, à arbitrer avec le métier :
#   ("Coat", "Leading Edge Erosion", 4) et (..., 5)
#   ("Laminate", "Leading Edge Erosion", 1) et (..., 2)
# =============================================================================
DAMAGE_TO_CATEGORY = damage_to_category = {
("Coat", "Leading Edge Erosion", 1): "b",
("Coat", "Leading Edge Erosion", 2): "c",
("Coat", "Leading Edge Erosion", 3): "c",
("Laminate", "Leading Edge Erosion", 3): "d",
("Laminate", "Leading Edge Erosion", 4): "e",
("Laminate", "Leading Edge Erosion", 5): "e",
("LE Paint", "Paint Erosion", 1): "b",
("LE Paint", "Paint Erosion", 2): "c",
("LE Paint", "Paint Erosion", 3): "c",
("LE Laminate", "Laminate Destroyed", 2): "d",
("LE Laminate", "Laminate Destroyed", 3): "d",
("LE Laminate", "Laminate Destroyed", 4): "e",
("LE Laminate", "Laminate Exposed", 2): "c",
("LE Laminate", "Laminate Exposed", 3): "d",
("LE Laminate", "Laminate Exposed", 4): "d"
}

# "a" = traité comme sain. Mettre None pour écarter la ligne à la place.
CATEGORIE_PAR_DEFAUT = "a"

# Combinaisons rencontrées et non couvertes, pour audit (clé -> nb d'occurrences)
COMBINAISONS_NON_MAPPEES = {}


def categorize_damage(df):
    df = df.copy()
    keys = list(zip(df["part_damaged"], df["defect_type"], df["criticality_id"]))

    for k in keys:
        if k not in DAMAGE_TO_CATEGORY:
            COMBINAISONS_NON_MAPPEES[k] = COMBINAISONS_NON_MAPPEES.get(k, 0) + 1

    df.loc[:, "excel_category"] = [
        DAMAGE_TO_CATEGORY.get(k, CATEGORIE_PAR_DEFAUT) for k in keys
    ]
    return df


# Mapping puis ajout de la colonne de catégorisation:


## Calcul de la longueur des dommages:
def calculate_damage_length(df):

#     df = df.copy()
#     def get_height(row):
#         xy_coordinates = row["xy_coordinates"]
#         pixel_height = abs(xy_coordinates[0][0][1] - xy_coordinates[0][2][1])
#         return pixel_height * row["conv_pixelh_m"]

#     df["damage_length_meters"] = df.apply(get_height, axis=1)
#     df["damage_length_meters"] = df["damage_length_meters"] / 1000  # convert to meters

    def get_height(xy_coords):
        try:
            return abs(xy_coords[0][0][1] - xy_coords[0][2][1])
        except (IndexError, TypeError):
            print("ERROR: calculate_damage_length")
            return np.nan

    pixel_heights = df["xy_coordinates"].map(get_height)
    df["damage_length_meters"] = (pixel_heights * df["conv_pixelh_m"]) / 1000

    return df


## Turbine_info
# Pour une turbine
def turbine_info(df, site_id, turbine_id, planification_id):
    # Filter DataFrame based on site_id, turbine_id, planification_id
    df_filtered = df[
        (df["site_id"] == site_id)
        & (df["turbine_id"] == turbine_id)
        & (df["planification_id"] == planification_id)
    ]

    # Check if filtered DataFrame is empty
    if df_filtered.empty:
        return None

    # Extract required information
    power_rated = df_filtered["power_mw"].iloc[0] * 1000
    radius = df_filtered["diameter"].iloc[0] / 2
    ro = df_filtered["air_density"].iloc[0]
    A = df_filtered["weibull_A"].iloc[0]
    A_Jensen = df_filtered["weibull_A_Jensen"].iloc[0]
    k = df_filtered["weibull_k"].iloc[0]
    k_Jensen = df_filtered["weibull_k_Jensen"].iloc[0]
    P_specific = power_rated * 1000 / (math.pi * radius ** 2)
    Vave_Rayleigh = A * math.sqrt(math.pi) / 2

    # Create DataFrame for output
    data = {
        "site_id": [site_id],
        "turbine_id": [turbine_id],
        "planification_id": [planification_id],
        "Power rated": [power_rated],
        "Radius": [radius],
        "B(no of blades)": [3],
        "Ro": [ro],
        "Max tip speed": [80.0],
        "TSRopt": [9.00],
        "A": [A],
        "A_Jensen": [A_Jensen],
        "Vave (Rayleigh)": [Vave_Rayleigh],
        "k": [k],
        "k_Jensen": [k_Jensen],
        "P_specific": [P_specific],
        # 'CP_max': [((16/27)*(1-2/(9*3+2))*(1-(0.5*(1-math.sqrt(1-(8/9)))*0.94)))],
        # 'V_rated': [((A*math.sqrt(math.pi) / 2)/(0.5*ro*((16/27)*(1-2/(9*3+2))*(1-(0.5*(1-math.sqrt(1-(8/9)))*0.94)))))**(1/3)],
        "Drivetrain-eff.": [0.94],
        "cl_design": [113],
        "cl/cd_clean,70": [110],
        "cl/cd_clean,30": [78],
        "cl/cd_clean,0": [6],
    }

    df_output = pd.DataFrame(data)
    return df_output


# Pour toute les turbines:
def extract_all_turbine_info(df):
    # Get unique combinations of site_id, planification_id and turbine_id
    unique_combinations = df[
        ["site_id", "planification_id", "turbine_id"]
    ].drop_duplicates()

    # List to hold the DataFrames for each turbine
    turbine_info_list = []

    # Loop through each unique combination
    for _, row in unique_combinations.iterrows():
        site_id = row["site_id"]
        planification_id = row["planification_id"]
        turbine_id = row["turbine_id"]

        # Use the turbine_info function to get information
        df_info = turbine_info(df, site_id, turbine_id, planification_id)
        if df_info is not None:
            turbine_info_list.append(df_info)

    # Concatenate all the DataFrames
    final_df = pd.concat(turbine_info_list, ignore_index=True)

    return final_df


## Création df de dommages par radius:
# Fonctions pour créer la table (de 0 à 30% de radius, et de 30% à 100% tout les 2 %, peut-être modifier):
def create_blade_damage_df(diameter):
    r_R_values = np.concatenate((np.array([0, 0.3]), np.arange(0.32, 1.02, 0.02)))
    r_values = r_R_values * diameter / 2
    df_blade_damage = pd.DataFrame({"r/R": r_R_values, "r": r_values, "Category": "a"})
    return df_blade_damage


# Ajout d'un dictionnaire de mappage des catégories et des valeurs du facteur cl/cd associées à chaque dommages
category_cl_cd_factor_mapping = {"a": 1.0, "b": 0.9, "c": 0.7, "d": 0.5, "e": 0.3}
# Fonction qui mappe les dommages:
def get_turbine_damage(df, site_id, planification_id, turbine_id):
    turbine_data = df[
        (df["site_id"] == site_id)
        & (df["planification_id"] == planification_id)
        & (df["turbine_id"] == turbine_id)
    ]

    # PATCH #23 : .iloc[0] plantait si le filtre ne renvoyait rien
    if turbine_data.empty:
        raise ValueError(
            f"Aucune donnée pour site_id={site_id}, planification_id={planification_id}, turbine_id={turbine_id}"
        )

    grouped = turbine_data.groupby("blade")

    diametre = turbine_data["diameter"].iloc[0]
    blade_dfs = {
        "A": create_blade_damage_df(diametre),
        "B": create_blade_damage_df(diametre),
        "C": create_blade_damage_df(diametre),
    }
    # PATCH #23 : colonne toujours présente, même si la boucle ne la crée pas
    for blade in blade_dfs:
        blade_dfs[blade]["cl/cd-factor"] = 1.0

    for blade in ["A", "B", "C"]:
        if blade in grouped.groups:
            group = grouped.get_group(blade)
            
            for i, row in group.iterrows():
               
                radius = row["radius"]
                excel_category = row["excel_category"]
                damage_length = row["damage_length_meters"]

                # Add a new column with shifted radius values
                blade_dfs[blade]["r_next"] = blade_dfs[blade]["r"].shift(-1)

                # Create a new column to indicate whether each row is within the damage range
                blade_dfs[blade]["in_damage_range"] = (
                    blade_dfs[blade]["r"] <= radius + damage_length
                ) & (blade_dfs[blade]["r_next"] >= radius)

                # Update the category for the corresponding rows
                blade_dfs[blade]["Category"] = blade_dfs[blade].apply(
                    lambda x: excel_category if x["in_damage_range"] and excel_category > x["Category"] else x["Category"],
                    axis=1,
                )

                # Map 'cl/cd-factor' from 'Category' using the mapping dictionary
                blade_dfs[blade]["cl/cd-factor"] = blade_dfs[blade]["Category"].map(
                    category_cl_cd_factor_mapping
                )

                # Drop the added columns
                blade_dfs[blade] = blade_dfs[blade].drop(
                    columns=["r_next", "in_damage_range"]
                )
        else:
            # If blade does not have damages, simply set the 'cl/cd-factor' to 1.0
            blade_dfs[blade]["cl/cd-factor"] = 1.0

    final_df = pd.concat(blade_dfs.values(), axis=1, keys=blade_dfs.keys())

    return final_df


## Début de création du df_final:
def create_df(site_id, planification_id, turbine_id, df):
    # Retrieve turbine data
    # turbine_data = df[
    #     (df["site_id"] == site_id)
    #     & (df["planification_id"] == planification_id)
    #     & (df["turbine_id"] == turbine_id)
    # ].iloc[0]

    if df is None or df.empty:
        print(
            f"ERROR: Aucune donnée trouvée pour site_id={site_id}, planification_id={planification_id}, turbine_id={turbine_id}"
        )
        return pd.DataFrame()

    filtered_df = df[
        (df["site_id"] == int(site_id))
        & (df["planification_id"] == int(planification_id))
        & (df["turbine_id"] == int(turbine_id))
    ]

    if filtered_df.empty:
        print(
            f"ERROR: Aucune donnée trouvée pour site_id={site_id}, planification_id={planification_id}, turbine_id={turbine_id}"
        )
        return pd.DataFrame()

    turbine_data = filtered_df.iloc[0]

    weibull_A = turbine_data["weibull_A"]
    weibull_k = turbine_data["weibull_k"]
    weibull_A_Jensen = turbine_data["weibull_A_Jensen"]
    weibull_k_Jensen = turbine_data["weibull_k_Jensen"]

    # Create V column
    df_V = pd.DataFrame({"V": np.arange(27)})

    # =========================================================================
    # PATCH #10 - (V + V.shift(-1)) / 2 valait NaN sur la dernière ligne, donc
    # 'Weibull accum' et 'Weibull distr' étaient NaN à V = 26 : cette tranche
    # de vent ne contribuait jamais à l'AEP. V + 0.5 est identique partout
    # ailleurs et corrige la dernière ligne.
    # =========================================================================
    df_V["V_mean"] = df_V["V"] + 0.5
    df_V["Weibull accum"] = 1 - np.exp(-((df_V["V_mean"] / weibull_A) ** weibull_k))
    df_V["Weibull accum Jensen"] = 1 - np.exp(
        -((df_V["V_mean"] / weibull_A_Jensen) ** weibull_k_Jensen)
    )

    # Create Weibull distr column
    df_V["Weibull distr"] = df_V["Weibull accum"].diff()
    df_V["Weibull distr Jensen"] = df_V["Weibull accum Jensen"].diff()
    df_V.loc[0, "Weibull distr"] = df_V.loc[0, "Weibull accum"]
    df_V.loc[0, "Weibull distr Jensen"] = df_V.loc[0, "Weibull accum Jensen"]

    # Create TSR column
    df_V["TSR"] = df_V["V"].apply(
        lambda x: 9 if 9 * x < 80 else 80 / x if x != 0 else np.nan
    )

    # Create CT,opt column
    df_V["CT,opt"] = np.nan
    df_V.loc[1, "CT,opt"] = 8 / 9
    for i in range(2, len(df_V)):
        df_V.loc[i, "CT,opt"] = df_V.loc[i - 1, "CT,opt"] + (
            (
                (df_V.loc[i, "TSR"] ** 2)
                * 2
                * np.pi
                * (df_V.loc[i - 1, "TSR"] - df_V.loc[i, "TSR"])
                * (
                    0.0068 * df_V.loc[i, "TSR"] ** 3
                    - 0.203 * df_V.loc[i, "TSR"] ** 2
                    + 2.08 * df_V.loc[i, "TSR"]
                    - 7.71
                )
                / (180 / np.pi)
            )
            * 0.0675
            / (np.pi * 113)
        )

    # Create cl/cd_clean column
    df_V["cl/cd_clean"] = 110
    df_V.loc[[0, len(df_V) - 1], "cl/cd_clean"] = np.nan
    # Create a_inv column
    df_V["a_inv"] = 0.5 * (1 - np.sqrt(1 - df_V["CT,opt"]))

    return df_V.drop(columns="V_mean")


# Création d'un df augmenté avec chaque turbine_info + les distr de Weibull
def augment_turbine_info_with_weibull(df):
    # Get the basic turbine info using the extract_all_turbine_info function
    df_info = extract_all_turbine_info(df)

    # Initialize empty columns for Weibull data
    df_info["Weibull accum"] = None
    df_info["Weibull accum Jensen"] = None
    df_info["Weibull distr"] = None
    df_info["Weibull distr Jensen"] = None

    # For each row in df_info, get the Weibull data using the create_df function
    for idx, row in df_info.iterrows():
        df_weibull = create_df(
            row["site_id"], row["planification_id"], row["turbine_id"], df
        )

        df_info.at[idx, "Weibull accum"] = df_weibull["Weibull accum"].tolist()
        df_info.at[idx, "Weibull accum Jensen"] = df_weibull[
            "Weibull accum Jensen"
        ].tolist()
        df_info.at[idx, "Weibull distr"] = df_weibull["Weibull distr"].tolist()
        df_info.at[idx, "Weibull distr Jensen"] = df_weibull[
            "Weibull distr Jensen"
        ].tolist()

    return df_info



##########

def _calculate_ct(r_R_series, tsr_series, a_series):

    """ Fonction d'aide vectorisée pour calculer CT. """
    r_R_plus_1 = r_R_series.shift(-1)
    r_R_current = r_R_series
    # La logique de fillna reproduit le "if i > 1 else a_i_minus_1" de la boucle originale
    r_R_minus_1 = r_R_series.shift(1).fillna(r_R_current)

    term1 = ((r_R_plus_1 + r_R_current) / 2) ** 2
    term2 = ((r_R_current + r_R_minus_1) / 2) ** 2
    term3 = r_R_current ** (3 * tsr_series)
    
    ct_values = (
        np.pi
        * (term1 - term2)
        * 4
        * a_series
        * (1 - a_series)
        * (1 - term3)
    )
    return ct_values

def _calculate_cp(r_R_series, tsr_series, a_inv_series, cl_cd_series, 
                            factor, a_visc_series=None):
    """ Fonction d'aide vectorisée pour calculer les colonnes Cp."""
    r_R_plus_1 = r_R_series.shift(-1)
    r_R_current = r_R_series
    
    term_pow = 3 * tsr_series
    
    integral_term_2_plus_1 = r_R_plus_1**2 * (1 - 2 * (r_R_plus_1**term_pow) / (term_pow + 2))
    integral_term_2_current = r_R_current**2 * (1 - 2 * (r_R_current**term_pow) / (term_pow + 2))
    terme_A = integral_term_2_plus_1 - integral_term_2_current

    # Si a_visc_series n'est pas fourni, on est dans le cas "clean"
    a_series_lift = a_visc_series if a_visc_series is not None else a_inv_series
    
    lift_term_numerator = terme_A * (1 - a_series_lift)
    
    integral_term_3_plus_1 = r_R_plus_1**3 * (2/3 - 2 * (r_R_plus_1**term_pow) / (term_pow + 3))
    integral_term_3_current = r_R_current**3 * (2/3 - 2 * (r_R_current**term_pow) / (term_pow + 3))
    delta_integral_3 = integral_term_3_plus_1 - integral_term_3_current
    
    drag_term_numerator = tsr_series * delta_integral_3 / cl_cd_series
    
    terme_B = (lift_term_numerator - drag_term_numerator) / (1 - a_inv_series)
    
    # Application de la formule finale avec la structure correcte
    cp_values = terme_A - factor * terme_B
    
    return cp_values

def create_intermediate_table(site_id, planification_id, turbine_id, df, V,
                              turbine_data=None, df_damage=None, df_V_data=None):
    # =========================================================================
    # PERF #1 - turbine_data / df_damage / df_V_data ne dépendent PAS de V.
    # Avant, les trois étaient recalculés à CHAQUE appel de cette fonction :
    # avec 26 vitesses par turbine (V = 1..26), c'est turbine_info(),
    # get_turbine_damage() (qui fait un groupby + une boucle Python sur les
    # dommages) et create_df() (qui refait toute la distribution de Weibull)
    # qui tournaient chacun 26 fois pour un résultat identique à chaque fois.
    #
    # Les trois deviennent des paramètres optionnels : create_final_df() les
    # calcule une seule fois et les réutilise pour les 26 appels. La fonction
    # reste utilisable seule (les paramètres non fournis sont recalculés
    # comme avant), donc tout code existant qui l'appelle continue de marcher.
    # =========================================================================
    if turbine_data is None:
        turbine_data = turbine_info(df, site_id, turbine_id, planification_id)
    # PATCH #24 : turbine_info() renvoie None (et non un df vide) quand le
    # filtre ne trouve rien -> `None.empty` levait un AttributeError obscur.
    if turbine_data is None or turbine_data.empty:
        raise ValueError("Informations sur la turbine non trouvées.")

    if df_damage is None:
        df_damage = get_turbine_damage(df, site_id, planification_id, turbine_id)

    if df_V_data is None:
        df_V_data = create_df(site_id, planification_id, turbine_id, df)

    df_V_selected = df_V_data[df_V_data["V"] == V]
    if df_V_selected.empty:
        raise ValueError(f"La vitesse V={V} n'a pas été trouvée dans les données.")
        
    a_inv_value = df_V_selected["a_inv"].values[0]
    ct_opt_value = df_V_selected["CT,opt"].values[0]

    # Création du DataFrame de base
    r_R_values = np.concatenate((np.array([0, 0.3]), np.arange(0.32, 1.02, 0.02)))
    diameter = turbine_data["Radius"].values[0] * 2
    df_intermediate = pd.DataFrame({
        "r/R": r_R_values,
        "r": r_R_values * diameter / 2
    })
    df_intermediate["TSR"] = 9 if 9 * V < 80 else (80 / V if V != 0 else np.nan)

    # Calculs en boucle pour chaque pale
    BLADES = ["A", "B", "C"]
    cl_cd_clean_values = np.where(
        df_intermediate["r/R"] <= 0.3, 6, np.where(df_intermediate["r/R"] <= 0.7, 78, 110)
    )

    for blade in BLADES:
        # Définition des noms de colonnes
        col_factor = f"Blade {blade} // cl/cd-factor"
        col_cl_cd_clean = f"Blade {blade} // cl/cd-clean"
        col_a_inv = f"Blade {blade} // a,inv"
        col_ct_clean = f"Blade {blade} // CT,clean"
        col_cp_clean = f"Blade {blade} // Cp,clean"
        col_cl_cd = f"Blade {blade} // cl/cd"
        col_clvisc = f"Blade {blade} // clvisc/clinv,out"
        col_a_visc = f"Blade {blade} // a,visc"
        col_ct_ler = f"Blade {blade} // CT,LER"
        col_cp_ler = f"Blade {blade} // Cp,ler"

        # Calculs de base et pour "clean"
        df_intermediate[col_factor] = df_damage[blade]["cl/cd-factor"].values
        df_intermediate[col_cl_cd_clean] = cl_cd_clean_values
        df_intermediate[col_a_inv] = a_inv_value
        
        df_intermediate[col_ct_clean] = _calculate_ct(
            df_intermediate["r/R"], df_intermediate["TSR"], df_intermediate[col_a_inv]
        )
        
        # Cp,clean: Le facteur est 1
        df_intermediate[col_cp_clean] = _calculate_cp(
            r_R_series=df_intermediate["r/R"],
            tsr_series=df_intermediate["TSR"],
            a_inv_series=df_intermediate[col_a_inv],
            cl_cd_series=df_intermediate[col_cl_cd_clean],
            factor=1.0
        )

        # Calculs pour "LER"
        df_intermediate[col_cl_cd] = df_intermediate[col_factor] * df_intermediate[col_cl_cd_clean]
        df_intermediate[col_clvisc] = np.power(df_intermediate[col_factor], 1/3).where(df_intermediate[col_factor] < 1, 1)
        
        sqrt_arg = 1 - df_intermediate[col_clvisc] * ct_opt_value
        sqrt_arg[sqrt_arg < 0] = 0 
        df_intermediate[col_a_visc] = 0.5 * (1 - np.sqrt(sqrt_arg))

        df_intermediate[col_ct_ler] = _calculate_ct(
            df_intermediate["r/R"], df_intermediate["TSR"], df_intermediate[col_a_visc]
        )
        
        # Cp,ler: Le facteur est la colonne clvisc
        df_intermediate[col_cp_ler] = _calculate_cp(
            r_R_series=df_intermediate["r/R"],
            tsr_series=df_intermediate["TSR"],
            a_inv_series=df_intermediate[col_a_inv],
            cl_cd_series=df_intermediate[col_cl_cd],
            factor=df_intermediate[col_clvisc],
            a_visc_series=df_intermediate[col_a_visc]
        )

    # Réorganisation finale des colonnes
    # (Identique à avant)
    new_col_order = [
        "r/R", "r", "TSR",
        "Blade A // cl/cd-factor", "Blade B // cl/cd-factor", "Blade C // cl/cd-factor",
        "Blade A // cl/cd-clean", "Blade B // cl/cd-clean", "Blade C // cl/cd-clean",
        "Blade A // a,inv", "Blade B // a,inv", "Blade C // a,inv",
        "Blade A // Cp,clean", "Blade B // Cp,clean", "Blade C // Cp,clean",
        "Blade A // CT,clean", "Blade B // CT,clean", "Blade C // CT,clean",
        "Blade A // cl/cd", "Blade B // cl/cd", "Blade C // cl/cd",
        "Blade A // clvisc/clinv,out", "Blade B // clvisc/clinv,out", "Blade C // clvisc/clinv,out",
        "Blade A // a,visc", "Blade B // a,visc", "Blade C // a,visc",
        "Blade A // Cp,ler", "Blade B // Cp,ler", "Blade C // Cp,ler",
        "Blade A // CT,LER", "Blade B // CT,LER", "Blade C // CT,LER",
    ]
    final_cols = [col for col in new_col_order if col in df_intermediate.columns]

    return df_intermediate[final_cols]



def get_aep_loss_data(site_id, planification_id, turbine_id):
    df = get_aep_data(site_id, planification_id, turbine_id)

    if df.empty:
        print("Aucune donnée AEP trouvée pour ces paramètres.")
        return df


    return df



def get_aep_data(site_id, planification_id, turbine_id):
    # Requête unique qui récupère à la fois basic_data et données de aep_loss (weibull)
    query = """
        SELECT
            ir.id,
            ir.planification_id,
            p.date AS planification_date,
            p.site_id,
            s.name AS site_name,
            ir.turbine_id,
            t.name AS turbine_name,
            ct.name AS blade,
            ir.radius,
            c.label_en AS part_damaged,
            dt.label_en AS defect_type,
            ir.criticality_id,
            ir.xy_coordinates,
            ir.conv_pixelh_m,
            ir.conv_pixelw_m,
            t.altitude,
            t.latitude,
            t.longitude,
            m.name AS model_name,
            m.diameter AS diameter,
            t.power_mw,
            al.air_density,
            al.k_weibull AS "weibull_k",
            al.a_weibull AS "weibull_A",
            al.a_weibull_jensen AS "weibull_A_Jensen",
            al.k_weibull_jensen AS "weibull_k_Jensen"
        FROM (
            SELECT *
            FROM incident_records
            WHERE deleted_at IS NULL
              AND (
                  (defect_type_id = 41 AND part_id = 1)
                  OR (component_id IN (70, 71) AND defect_type_id = 97)
              )
        ) AS ir
        JOIN planifications p ON p.id = ir.planification_id
        JOIN sites s ON s.id = p.site_id
        JOIN turbines t ON t.id = ir.turbine_id
        JOIN components_turbines ct ON ct.id = ir.component_turbine_id
        JOIN models m ON m.id = t.model_name
        JOIN components c ON c.id = ir.component_id
        JOIN defect_types dt ON dt.id = ir.defect_type_id
        LEFT JOIN aep_loss al ON al.planification_id = ir.planification_id
                              AND al.turbine_id = ir.turbine_id
        WHERE ir.conv_pixelh_m IS NOT NULL
          AND ir.conv_pixelw_m IS NOT NULL
          AND p.site_id = %s
          AND ir.planification_id = %s
          AND ir.turbine_id = %s
        --ORDER BY ir.id, blade, ir.radius, part_damaged;
    """
    df = query_db(query, (site_id, planification_id, turbine_id))
    return df



def query_db(query, params):
    try:
        with TW_DB_CURSOR.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params)
            results = cursor.fetchall()
        return pd.DataFrame(results) if results else pd.DataFrame()
    except psycopg2.Error as e:
        print("Erreur lors de l'exécution de la requête :", e)
        return pd.DataFrame()


# PATCH #28 : le cache renvoyait le MÊME objet DataFrame à chaque appel ;
# les filtres appliqués ensuite travaillaient donc sur une vue partagée.
# On renvoie désormais une copie, et le cache est borné.
CASH = {}
CASH_MAX = 512


def get_aep_loss_turbine(
    site_id, planification_id, turbine_id, delete_damage=None, not_blades=None, criticality_id=None
):  
    
    key = (site_id, planification_id, turbine_id)
    if key in CASH:
        df = CASH[key].copy()          # PATCH #28
    else:
        df = get_aep_loss_data(site_id, planification_id, turbine_id)
        if len(CASH) >= CASH_MAX:      # PATCH #28 : cache borné
            CASH.pop(next(iter(CASH)))
        CASH[key] = df
        df = df.copy()

    if df.empty:
        return None

    if isinstance(criticality_id, (int, float)):
        df = df[df["criticality_id"] >= criticality_id]

    # =========================================================================
    # PATCH #26 - deux bugs ici :
    #   a) args=(i) n'est PAS un tuple : pandas dépaquetait la liste et
    #      passait chaque élément comme argument séparé -> TypeError.
    #      La forme correcte est args=(i,).
    #   b) `elif not_blades` : dès que delete_damage était fourni, le filtre
    #      not_blades était purement ignoré. Les deux filtres sont maintenant
    #      indépendants.
    # =========================================================================
    if delete_damage:
        if isinstance(delete_damage, list):
            for element in delete_damage:
                i = convert_to_int(element)
                df = df[df["xy_coordinates"].apply(verif, args=(i,))]
        else:
            df = df[df["blade"].apply(lambda x: x != delete_damage[-1])]

    if not_blades:
        blades_to_remove = {b[-1].upper() for b in not_blades}
        df = df[~df["blade"].isin(blades_to_remove)]

    
    if df.empty:
        return {
            "AEP_clean_Jensen": np.nan,
            "AEP_LER_Jensen": np.nan,
            "AEP_loss_Jensen": np.nan,
            "percent_Jensen": np.nan,
        }


    # =========================================================================
    # PATCH #27 - le LEFT JOIN sur aep_loss peut ne rien ramener : les colonnes
    # Weibull / air_density sortent alors en NULL et le calcul produisait des
    # NaN silencieux en bout de chaîne. On le détecte ici.
    # =========================================================================
    colonnes_requises = [
        "weibull_A", "weibull_k", "weibull_A_Jensen", "weibull_k_Jensen",
        "air_density", "diameter", "power_mw", "radius", "conv_pixelh_m",
    ]
    manquantes = [c for c in colonnes_requises if c not in df.columns or df[c].isna().any()]
    if manquantes:
        print(
            f"Données incomplètes pour site_id={site_id}, planification_id={planification_id}, "
            f"turbine_id={turbine_id} : {manquantes}"
        )
        return {
            "AEP_clean_Jensen": np.nan,
            "AEP_LER_Jensen": np.nan,
            "AEP_loss_Jensen": np.nan,
            "percent_Jensen": np.nan,
        }

    # calculer AEP_loss par pale avec compute AEP value soit : trier data par pale df_A = df['blade'] == (A | B | C)
    df = categorize_damage(df)

    df = calculate_damage_length(df)

    #start_time = time.time()
    result = compute_AEP_values(site_id, planification_id, turbine_id, df=df)

    return {
        "AEP_clean_Jensen": result[4],
        "AEP_LER_Jensen": result[5],
        "AEP_loss_Jensen": result[6],
        "percent_Jensen": result[7],
    }


def verif(a, b):
    a = convert_to_int(a)
    if a[0] == b:
        return False
    if a == b:
        return False
    return True


def convert_to_int(lst):
    """
    Convertit toutes les valeurs de la liste en entiers, y compris les sous-listes.
    """
    if isinstance(lst, list):
        return [convert_to_int(x) for x in lst]
    else:
        try:
            return int(lst)
        except ValueError:
            return lst

## Fin de la création du df turbine_info:
def turbine_info_bis(df, site_id, turbine_id, planification_id):
    df_output = turbine_info(df, site_id, turbine_id, planification_id)

    # PERF #2 : df_output contient déjà 'Radius', la seule colonne utilisée par
    # create_intermediate_table pour turbine_data -> on lui passe directement
    # au lieu de la laisser rappeler turbine_info() en interne.
    df_temp = create_intermediate_table(
        site_id, planification_id, turbine_id, df, 1, turbine_data=df_output
    )
    cploss_clean = (
        (df_temp["Blade A // Cp,clean"].sum()
        + df_temp["Blade B // Cp,clean"].sum()
        + df_temp["Blade C // Cp,clean"].sum()) / 3
    )
    df_output["CP_max"] = (
        (16 / 27)
        * (1 - 2 / ((df_output["TSRopt"].values[0]) * 3 + 2))
        * (1 - cploss_clean)
        * df_output["Drivetrain-eff."].values[0]
    )

    # Create V_rated column:
    df_output["V_rated"] = (
        (df_output["P_specific"].values[0])
        / (0.5 * (df_output["Ro"].values[0]) * (df_output["CP_max"].values[0]))
    ) ** (1 / 3)

    return df_output


## Fin de la création du df_final:
def create_final_df(site_id, planification_id, turbine_id, df):

    df_final = create_df(site_id, planification_id, turbine_id, df)

    # Utilisation de turbine_info:
    turbine_data = turbine_info_bis(df, site_id, turbine_id, planification_id)
    TSRopt = turbine_data["TSRopt"].values[0]
    B_no_of_blades = turbine_data["B(no of blades)"].values[0]
    drivetrain_eff = turbine_data["Drivetrain-eff."].values[0]
    Ro = turbine_data["Ro"].values[0]
    P_specific = turbine_data["P_specific"].values[0]
    Radius = turbine_data["Radius"].values[0]
    CP_max = turbine_data["CP_max"].values[0]
    Power_rated = turbine_data["Power rated"].values[0]
    V_rated = turbine_data["V_rated"].values[0]

    # =========================================================================
    # PERF #3 - même optimisation que PERF #1, côté appelant : df_damage ne
    # dépend pas de V, on le calcule une seule fois. turbine_data (déjà
    # calculé ci-dessus via turbine_info_bis, colonne 'Radius' incluse) et
    # df_final (qui EST déjà create_df(...), donc peut servir de df_V_data)
    # sont réutilisés tels quels : zéro appel supplémentaire à turbine_info(),
    # get_turbine_damage() ou create_df() dans la boucle des 26 vitesses.
    #
    # PATCH #11 : range(1, 26) s'arrêtait à V = 25 ; la tranche V = 26 restait
    # à NaN alors que sa probabilité Weibull n'est pas nulle.
    # =========================================================================
    df_damage = get_turbine_damage(df, site_id, planification_id, turbine_id)

    intermediate_tables = {
        i: create_intermediate_table(
            site_id, planification_id, turbine_id, df, i,
            turbine_data=turbine_data, df_damage=df_damage, df_V_data=df_final,
        )
        for i in range(1, len(df_final))
    }
    
    
    # Optimisé
    df_final["CPloss,loc,clean"] = np.nan
    for i, df_temp in intermediate_tables.items():
        cploss_clean = (
            (df_temp["Blade A // Cp,clean"].sum() +
            df_temp["Blade B // Cp,clean"].sum() +
            df_temp["Blade C // Cp,clean"].sum()) / 3
        )
        df_final.loc[i, "CPloss,loc,clean"] = cploss_clean


    # Création de la colonne "Cpopt-Cpact":
    df_final["Cpopt-Cpact"] = (
        16 / 27 - 4 * df_final["a_inv"] * (1 - df_final["a_inv"]) ** 2
    ) * (1 - 2 / (TSRopt * B_no_of_blades + 2))

    # =========================================================================
    # PATCH #13 - facteur de perte en bout de pale
    # Avant : (1 - 2 / (TSRopt - B_no_of_blades + 2))  ->  1 - 2/8
    # alors que CP_max (turbine_info_bis) et "Cpopt-Cpact" utilisent
    # (TSRopt * B + 2)  ->  1 - 2/29. Le '-' au lieu du '*' biaisait CP_clean
    # et CP_LER sur toutes les turbines.
    # =========================================================================
    facteur_bout_de_pale = 1 - 2 / (TSRopt * B_no_of_blades + 2)

    # Création de la colonne "CP_clean":
    df_final["CP_clean"] = (
        (16 / 27)
        * facteur_bout_de_pale
        * (1 - df_final["CPloss,loc,clean"])
        - df_final["Cpopt-Cpact"]
    ) * drivetrain_eff

    # Création de la colonne "Ps":
    df_final["Ps"] = np.minimum(
        0.5 * Ro * df_final["V"] ** 3 * df_final["CP_clean"], P_specific
    )

    # Création de la colonne "P":
    df_final["P"] = df_final["Ps"] * np.pi * Radius ** 2 / 1000

    # Création de la colonne "CP_clean,plot":
    df_final["CP_clean,plot"] = np.where(
        df_final["Ps"] < P_specific,
        df_final["CP_clean"],
        df_final["Ps"] / (0.5 * Ro * df_final["V"] ** 3),
    )

    # Création de la colonne "CTapprox":
    CP_ratio = (df_final["CP_clean,plot"] / CP_max) * (16 / 27)
    df_final["CTapprox"] = (
        20.553 * CP_ratio ** 4
        - 21.23 * CP_ratio ** 3
        + 7.3631 * CP_ratio ** 2
        + 0.2783 * CP_ratio
        + 0.0003
    )

    for i, df_temp in intermediate_tables.items():
        ctloss_clean = (
            (df_temp["Blade A // CT,clean"].sum()
            + df_temp["Blade B // CT,clean"].sum()
            + df_temp["Blade C // CT,clean"].sum()) / (np.pi * B_no_of_blades)
        )
        df_final.loc[i, "CT,rotor,clean"] = np.where(
            df_final.loc[i, "P"] < Power_rated,
            ctloss_clean,
            df_final.loc[i, "CTapprox"],
        )

    # Création de la colonne "CPloss,loc,LER":
    df_final["CPloss,loc,LER"] = np.nan
    for i, df_temp in intermediate_tables.items():
        cploss_LER = (
            (df_temp["Blade A // Cp,ler"].sum()
            + df_temp["Blade B // Cp,ler"].sum()
            + df_temp["Blade C // Cp,ler"].sum()) / 3
        )
        df_final.loc[i, "CPloss,loc,LER"] = cploss_LER


    # Création de la colonne "CP_LER":
    df_final["CP_LER"] = (
        (16 / 27)
        * facteur_bout_de_pale          # PATCH #13
        * (1 - df_final["CPloss,loc,LER"])
        - df_final["Cpopt-Cpact"]
    ) * drivetrain_eff

    # Création de la colonne "Ps_LER":
    df_final["Ps_LER"] = np.minimum(
        0.5 * Ro * df_final["V"] ** 3 * df_final["CP_LER"], P_specific
    )

    # Création de la colonne "P_LER":
    df_final["P_LER"] = df_final["Ps_LER"] * np.pi * Radius ** 2 / 1000

    # PATCH #14 : à V = 0 la puissance vaut 0, pas NaN. Sans cela la courbe
    # de puissance exportée contient des NaN, que les traitements aval
    # supprimaient de la chaîne JSON en décalant tout le vecteur.
    df_final.loc[0, ["Ps", "P", "Ps_LER", "P_LER"]] = 0.0

    # PATCH #15 : cette colonne utilisait Ps et CP_clean (copier-coller de
    # CP_clean,plot) au lieu de Ps_LER et CP_LER.
    df_final["CP_LER,plot"] = np.where(
        df_final["Ps_LER"] < P_specific,
        df_final["CP_LER"],
        df_final["Ps_LER"] / (0.5 * Ro * df_final["V"] ** 3),
    )

    for i, df_temp in intermediate_tables.items():
        ctloss_ler = (
            (df_temp["Blade A // CT,LER"].sum()
            + df_temp["Blade B // CT,LER"].sum()
            + df_temp["Blade C // CT,LER"].sum()) / (np.pi * B_no_of_blades)
        )
        df_final.loc[i, "CT,rotor,LER"] = np.where(
            df_final.loc[i, "P_LER"] < Power_rated,
            ctloss_ler,
            df_final.loc[i, "CTapprox"],
        )

    # Création de la colonne "CPloss,tot":
    df_final["CPloss,tot"] = np.where(
        df_final["Ps"] < P_specific,
        df_final["CPloss,loc,LER"] - df_final["CPloss,loc,clean"],
        0,
    )

    # Création de la colonne "Energy,clean":
    df_final["Energy,clean"] = df_final["P"] * df_final["Weibull distr"]
    df_final["Energy,clean,Jensen"] = df_final["P"] * df_final["Weibull distr Jensen"]

    # Créationde la colonne "Energy,LER":
    df_final["Energy,LER"] = df_final["P_LER"] * df_final["Weibull distr"]
    df_final["Energy,LER,Jensen"] = df_final["P_LER"] * df_final["Weibull distr Jensen"]

    return df_final


def compute_AEP_values(site_id, planification_id, turbine_id, df):
    # =========================================================================
    # PATCH #25 - le test portait sur TOUT le dataframe : un seul dommage non
    # mesurable, n'importe où, renvoyait NaN pour la turbine demandée alors
    # qu'elle était parfaitement calculable. Le test est désormais restreint
    # à la combinaison (site, planification, turbine).
    # =========================================================================
    masque = (
        (df["site_id"] == site_id)
        & (df["planification_id"] == planification_id)
        & (df["turbine_id"] == turbine_id)
    )
    df_turbine = df[masque]
    if df_turbine.empty or df_turbine["damage_length_meters"].isna().any():
        return (np.nan,) * 8

    df_AEP = create_final_df(site_id, planification_id, turbine_id, df)
    
    # Calcul des valeurs de l'AEP
    factor = 24 * 365 / 1000000
    AEP_clean = df_AEP["Energy,clean"].sum() * factor  # en GWh
    AEP_clean_Jensen = df_AEP["Energy,clean,Jensen"].sum() * factor  # en GWh
    AEP_LER = df_AEP["Energy,LER"].sum() * factor  # en GWh
    AEP_LER_Jensen = df_AEP["Energy,LER,Jensen"].sum() * factor  # en GWh
    AEP_loss = (AEP_clean - AEP_LER) * 1000  # en MWh
    AEP_loss_Jensen = (AEP_clean_Jensen - AEP_LER_Jensen) * 1000  # en MWh
    percent = (1 - (AEP_LER / AEP_clean)) * 100  # en %
    percent_Jensen = (1 - (AEP_LER_Jensen / AEP_clean_Jensen)) * 100  # en %

    return (
        AEP_clean,
        AEP_LER,
        AEP_loss,
        percent,
        AEP_clean_Jensen,
        AEP_LER_Jensen,
        round(AEP_loss_Jensen,2),
        percent_Jensen,
    )