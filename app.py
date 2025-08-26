import streamlit as st
import pandas as pd
from fetch_roles import get_azure_roles, get_teams_roles, get_intune_roles, get_purview_roles

st.set_page_config(page_title="Microsoft Roles Explorer", layout="wide")
st.title("Microsoft Roles Explorer")

# --- Token OAuth sécurisé ---
token = st.secrets["graph_token"]  # Token Microsoft Graph

# --- Sélection du service ---
service_options = ["Azure", "Teams", "Intune", "Purview", "Tous"]
service_selected = st.selectbox("Choisir un service", service_options)

# --- Paramètres spécifiques aux services ---
team_id = ""
purview_account = ""

if service_selected == "Teams" or service_selected == "Tous":
    team_id = st.text_input("ID de l'équipe Teams")

if service_selected == "Purview" or service_selected == "Tous":
    purview_account = st.text_input("Nom du compte Purview")

# --- Charger les rôles ---
if st.button("Charger les rôles"):
    roles_list = []

    try:
        if service_selected in ["Azure", "Tous"]:
            roles_list.extend(get_azure_roles(token))

        if service_selected in ["Teams", "Tous"]:
            if team_id:
                roles_list.extend(get_teams_roles(token, team_id))
            elif service_selected == "Teams":
                st.warning("Veuillez entrer un ID d'équipe Teams.")
        
        if service_selected in ["Intune", "Tous"]:
            roles_list.extend(get_intune_roles(token))

        if service_selected in ["Purview", "Tous"]:
            if purview_account:
                roles_list.extend(get_purview_roles(token, purview_account))
            elif service_selected == "Purview":
                st.warning("Veuillez entrer le nom du compte Purview.")

        # --- Affichage dans un DataFrame ---
        if roles_list:
            df = pd.DataFrame(roles_list)
            st.dataframe(df, use_container_width=True)

            # --- Filtrage par texte ---
            search_text = st.text_input("Rechercher dans les rôles...")
            if search_text:
                df_filtered = df[df.apply(lambda row: row.astype(str).str.contains(search_text, case=False).any(), axis=1)]
                st.dataframe(df_filtered, use_container_width=True)
            else:
                df_filtered = df

            # --- Export CSV ---
            csv = df_filtered.to_csv(index=False)
            st.download_button("Exporter CSV", csv, "roles.csv")
        else:
            st.info("Aucun rôle trouvé pour ce service.")

    except Exception as e:
        st.error(f"Erreur lors de la récupération des rôles : {e}")
