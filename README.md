# 🌧️ MvuaSmart

**An El Niño Farm Advisor for Kenyan Smallholder Farmers**

MvuaSmart is an intelligent agricultural advisory platform designed to help Kenyan smallholder farmers and pastoralists navigate extreme weather events, specifically El Niño. By translating complex meteorological forecasts into hyper-local, actionable advice via USSD and SMS, MvuaSmart bridges the gap between regional weather data and practical on-farm decision-making.

## 🚀 Features

*   **USSD & SMS Delivery:** Accessible on any basic feature phone. Farmers receive simple, localized alerts (e.g., "Harvest before Thursday. Heavy rain is coming") and can report on-the-ground conditions (flooding, crop damage).
*   **AI Advisory Engine:** Powered by OCI Generative AI to translate raw weather data into practical farming advice in both English and Swahili.
*   **Weather API Integrations:** Fetches real-time and forecasted weather data to anticipate heavy rains and dry spells.
*   **Accessible Authentication:** Frictionless, secure login for farmers and admins using **Mobile Number + PIN**.
*   **County & Stakeholder Dashboard:** A live map/dashboard highlighting high-risk areas to help county officers, insurers, and cooperatives deploy resources efficiently.

## 🏗️ System Architecture

The repository is divided into two major directories: a web frontend for county officials/stakeholders, and a robust Django backend that orchestrates the USSD gateway, AI engine, and APIs.

*   **Backend:** Django, Django REST Framework (DRF)
*   **Frontend:** React / Next.js (or your chosen framework)
*   **Database:** Oracle Autonomous Database (or PostgreSQL/SQLite for local dev)
*   **AI Engine:** Oracle Cloud Infrastructure (OCI) Generative AI
*   **Dashboards:** Web Frontend / Oracle APEX 
*   **USSD Gateway:** Configured via webhooks (e.g., Africa's Talking)

## 📁 Repository Structure

```text
MvuaSmart/
├── backend/                   # Django application
│   ├── manage.py
│   ├── mvuasmart_core/        # Project settings and main routing
│   ├── accounts/              # Custom Auth: Mobile Number & PIN
│   ├── ussd/                  # USSD menus, session state, and webhooks
│   ├── weather/               # External Weather API integrations
│   ├── ai_engine/             # OCI Generative AI prompt handling & translation
│   └── requirements.txt
├── frontend/                  # Web dashboard UI
│   ├── package.json
│   ├── public/
│   └── src/
└── README.md
