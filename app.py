import streamlit as st
from fetch_roles import get_azure_roles, get_teams_roles, get_intune_roles, get_purview_roles
from utils import roles_to_dataframe
import pandas as pd

st.set_page_config(page_title="Microsoft Roles Explorer", layout="wide")
st.title("Microsoft Roles Explorer")

token = st.secrets["graph_token"]  # OAuth token pour Microsoft Graph
service = st.selectbox("Choisir un service", ["Azure", "Teams", "Intune", "Purview"])

roles = []

if st.button("Charger les rôles"):
    try:
        if service == "Azure":
            roles = get_azure_roles(token)
        elif service == "Teams":
            team_id = st.text_input("ID de l'équipe")
            if not team_id:
                st.warning("Veuillez entrer un ID d'équipe Teams.")
            else:
                roles = get_teams_roles(token, team_id)
        elif service == "Intune":
            roles = get_intune_roles(token)
        elif service == "Purview":
            account_name = st.text_input("Nom du compte Purview")
            if not account_name:
                st.warning("Veuillez entrer le nom du compte Purview.")
            else:
                roles = get_purview_roles(token, account_name)

        df = roles_to_dataframe(roles)
        if not df.empty:
            st.dataframe(df)
            csv = df.to_csv(index=False)
            st.download_button("Exporter CSV", csv, "roles.csv")
        else:
            st.info("Aucun rôle trouvé pour ce service.")

    except Exception as e:
        st.error(f"Erreur lors de la récupération des rôles : {e}")
