import streamlit as st
import pandas as pd
from fetch_roles import get_azure_roles, get_teams_roles

st.title("Microsoft Roles Explorer")

service = st.selectbox("Choisir un service", ["Azure", "Teams"])
token = st.secrets["graph_token"]

if st.button("Charger les rôles"):
    if service == "Azure":
        roles = get_azure_roles(token)
        df = pd.DataFrame([{"Name": r["properties"]["roleName"], 
                            "Description": r["properties"]["description"]} 
                           for r in roles])
    elif service == "Teams":
        team_id = st.text_input("ID de l'équipe")
        if team_id:
            roles = get_teams_roles(token, team_id)
            df = pd.DataFrame([{"User": r["displayName"], "Role": r["roles"][0]} for r in roles])
    st.dataframe(df)
    st.download_button("Exporter CSV", df.to_csv(index=False), "roles.csv")
