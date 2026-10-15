"""Alert catalogue (ARCHITECTURE §5) and the vetted Swahili/English copy the backend sends.

Order of precedence when rendering an alert (alerts/templates.py):
  1. ALERT_TEMPLATE row for (hazard, severity, lang)   <- edited by officers in APEX page 11
  2. ALERT_TYPES.template_sw / template_en
  3. DEFAULT_ALERT_COPY below
Every language's copy must be checked by a native speaker before real use (API_CONTRACT §4).
Slots: {ward} {day} {confidence} {point} {time} {fee}.
"""

# Seed rows for ALERT_TYPES (manage.py seed_reference inserts the missing ones only).
ALERT_TYPES = [
    # code, service, name, act, watch, cooldown_h, overrides_cap, audience
    ("A1", "FARM", "Dry window: harvest now", 0.70, 0.50, 48, False, "farmer,both"),
    ("A2", "FARM", "Heavy rain within 48 h", 0.60, 0.40, 48, False, "farmer,both,operator"),
    ("A3", "FARM", "Aflatoxin risk", 0.70, 0.50, 72, False, "farmer,both"),
    ("A4", "FARM", "Cropland flood within 3 days", 0.60, 0.40, 48, True, "farmer,both"),
    ("A5", "FARM", "Dryer booking at collection point", None, None, 12, True, "farmer,both"),
    ("B1", "HERD", "River flood (GloFAS > 5-yr level)", 0.30, 0.15, 48, True, "herder,both"),
    ("B2", "HERD", "Flash flood within 2 days", 0.60, 0.40, 48, True, "herder,both"),
    ("B3", "HERD", "Rift Valley fever: vaccinate", 0.60, 0.35, 168, True, "herder,both"),
    ("B4", "HERD", "Drought: grazing and sell timing", 0.60, 0.40, 168, False, "herder,both,committee"),
    ("C1", "HEALTH", "Cholera risk after floods", 0.60, 0.40, 72, True, "health_promoter"),
    ("C2", "HEALTH", "RVF human risk", 0.60, 0.35, 168, True, "health_promoter"),
    ("C3", "HEALTH", "Aflatoxin food-safety risk", 0.70, 0.50, 168, False, "health_promoter"),
    # Seasonal outlook (seasonal/, v3 model). Watch threshold left empty: the model's own threshold applies.
    ("S1", "FARM", "Seasonal outlook: very wet short rains", 0.50, None, 1440, False, "farmer,both"),
]

DEFAULT_AUDIENCE = {
    "FARM": ["farmer", "both"],
    "HERD": ["herder", "both"],
    "HEALTH": ["health_promoter"],
}

# ALERT_TEMPLATE.hazard used for each alert type. Override with NGAO_TEMPLATE_HAZARDS (JSON) to
# match the hazard names Chris used on the Alert Templates page.
DEFAULT_HAZARDS = {
    "A1": "dry_window",
    "A2": "heavy_rain",
    "A3": "aflatoxin",
    "A4": "field_flood",
    "A5": "dryer_booking",
    "B1": "river_flood",
    "B2": "flash_flood",
    "B3": "rvf",
    "B4": "drought",
    "C1": "cholera",
    "C2": "rvf_human",
    "C3": "aflatoxin_food",
    "S1": "seasonal_wet",
}

DEFAULT_ALERT_COPY = {
    "A1": {
        "sw": "MvuaSmart: Siku 3 za jua zinatarajiwa kuanzia {day} katika kata yako (uhakika {confidence}%). Vuna mahindi sasa na uyakaushe. Jibu NDIYO ukivuna.",
        "en": "MvuaSmart: 3 dry days expected from {day} in your ward ({confidence}% sure). Harvest your maize now and dry it. Reply NDIYO when you harvest.",
    },
    "A2": {
        "sw": "MvuaSmart: Mvua kubwa inatarajiwa ndani ya saa 48 kata ya {ward} (uhakika {confidence}%). Funika mahindi yaliyovunwa. Jibu idadi ya magunia mabichi.",
        "en": "MvuaSmart: Heavy rain expected within 48 hours in {ward} ({confidence}% sure). Cover harvested maize. Reply with the number of wet bags.",
    },
    "A3": {
        "sw": "MvuaSmart: Hatari ya sumukuvu (aflatoxin) iko juu kata ya {ward} (uhakika {confidence}%). Kausha mahindi hadi unyevu 13% kabla ya kuhifadhi.",
        "en": "MvuaSmart: High aflatoxin risk in {ward} ({confidence}% sure). Dry maize to 13% moisture before you store it.",
    },
    "A4": {
        "sw": "MvuaSmart: Mafuriko mashambani yanawezekana ndani ya siku 3 kata ya {ward} (uhakika {confidence}%). Hamisha mavuno na pembejeo mahali pa juu.",
        "en": "MvuaSmart: Field flooding possible within 3 days in {ward} ({confidence}% sure). Move harvest and inputs to higher ground.",
    },
    "A5": {
        "sw": "MvuaSmart: Mashine ya kukausha itafika {point} {day} {time}. Ada KES {fee} kwa gunia. Jibu 1 kuthibitisha.",
        "en": "MvuaSmart: A maize dryer will be at {point} on {day} at {time}. Fee KES {fee} per bag. Reply 1 to confirm.",
    },
    "B1": {
        "sw": "Ngao Mifugo: Mto unatarajiwa kufurika karibu na {ward} (uhakika {confidence}%). Hamisha mifugo mbali na kingo za mto sasa.",
        "en": "Ngao Mifugo: River flooding expected near {ward} ({confidence}% sure). Move livestock away from the riverbanks now.",
    },
    "B2": {
        "sw": "Ngao Mifugo: Mafuriko ya ghafla yanawezekana ndani ya siku 2 kata ya {ward} (uhakika {confidence}%). Epuka kulisha mifugo kwenye korongo.",
        "en": "Ngao Mifugo: Flash floods possible within 2 days in {ward} ({confidence}% sure). Keep livestock out of gullies and dry riverbeds.",
    },
    "B3": {
        "sw": "Ngao Mifugo: Hatari ya homa ya Bonde la Ufa (RVF) inaongezeka kata ya {ward} (uhakika {confidence}%). Chanja mifugo sasa. Jibu RVF ukiona dalili.",
        "en": "Ngao Mifugo: Rift Valley fever risk is rising in {ward} ({confidence}% sure). Vaccinate livestock now. Reply RVF if you see signs.",
    },
    "B4": {
        "sw": "Ngao Mifugo: Ukame unatarajiwa kata ya {ward} ndani ya wiki 4-8 (uhakika {confidence}%). Panga malisho na fikiria kuuza mifugo mapema.",
        "en": "Ngao Mifugo: Drought expected in {ward} within 4-8 weeks ({confidence}% sure). Plan grazing and consider selling animals early.",
    },
    "C1": {
        "sw": "Ngao Afya: Hatari ya kipindupindu iko juu kata ya {ward} (uhakika {confidence}%). Himiza kuchemsha maji na kunawa mikono kwa sabuni.",
        "en": "Ngao Afya: High cholera risk in {ward} ({confidence}% sure). Promote boiling drinking water and handwashing with soap.",
    },
    "C2": {
        "sw": "Ngao Afya: Hatari ya RVF kwa binadamu inaongezeka kata ya {ward} (uhakika {confidence}%). Himiza kuepuka maziwa mabichi na nyama isiyopikwa.",
        "en": "Ngao Afya: Human RVF risk is rising in {ward} ({confidence}% sure). Advise avoiding raw milk and undercooked meat.",
    },
    "C3": {
        "sw": "Ngao Afya: Hatari ya sumukuvu kwenye chakula kata ya {ward} (uhakika {confidence}%). Kagua mahindi kwenye masoko na maghala.",
        "en": "Ngao Afya: Aflatoxin food-safety risk in {ward} ({confidence}% sure). Inspect maize in markets and stores.",
    },
    "S1": {
        "sw": "MvuaSmart: Mvua za Okt-Des kata ya {ward} huenda zikazidi kawaida sana (uwezekano {confidence}%). Chimba mitaro, panda kwenye matuta, hifadhi nafaka pakavu.",
        "en": "MvuaSmart: Oct-Dec rains in {ward} may be far above normal ({confidence}% chance). Dig drainage, plant on raised beds, keep stored grain dry.",
    },
}

# Non-alert SMS the backend sends (registration, keywords, bookings, operator stop list).
SYSTEM_COPY = {
    "welcome": {
        "sw": "Ngao: Karibu! Utapokea tahadhari za kata ya {ward}. Jibu STOP kusitisha. Tuma LUGHA EN kwa Kiingereza.",
        "en": "Ngao: Welcome! You will get alerts for {ward} ward. Reply STOP to end. Send LANG SW for Kiswahili.",
    },
    "stopped": {
        "sw": "Ngao: Umejiondoa na hutapokea tahadhari tena. Tumia menyu ya USSD kujiandikisha upya.",
        "en": "Ngao: You have unsubscribed and will get no more alerts. Use the USSD menu to register again.",
    },
    "lang_set": {
        "sw": "Ngao: Lugha yako sasa ni Kiswahili.",
        "en": "Ngao: Your language is now English.",
    },
    "lang_usage": {
        "sw": "Ngao: Tuma LUGHA SW kwa Kiswahili au LUGHA EN kwa Kiingereza.",
        "en": "Ngao: Send LANG EN for English or LANG SW for Kiswahili.",
    },
    "report_ack": {
        "sw": "Ngao: Asante, tumepokea ripoti yako.",
        "en": "Ngao: Thank you, we have received your report.",
    },
    "booking_confirmed": {
        "sw": "MvuaSmart: Asante! Nafasi ya kukausha magunia {bags} imethibitishwa: {point}, {day} {time}.",
        "en": "MvuaSmart: Thank you! Drying for {bags} bags is confirmed: {point}, {day} at {time}.",
    },
    "no_booking": {
        "sw": "Ngao: Hakuna nafasi ya kukausha inayosubiri jibu lako kwa sasa.",
        "en": "Ngao: There is no drying slot waiting for your reply right now.",
    },
    "operator_route": {
        "sw": "Ngao: Ratiba ya mashine {dryer} {day}: {stops}",
        "en": "Ngao: Route for dryer {dryer} on {day}: {stops}",
    },
}

# Short phrases for GET /ussd/ward-status (menu 3, "Hali ya kata yangu").
WARD_STATUS_PHRASES = {
    "A1": {"sw": "siku za jua", "en": "dry days ahead"},
    "A2": {"sw": "mvua kubwa", "en": "heavy rain"},
    "A3": {"sw": "hatari ya sumukuvu", "en": "aflatoxin risk"},
    "A4": {"sw": "mafuriko mashambani", "en": "field flooding"},
    "A5": {"sw": "mashine ya kukausha", "en": "maize dryer visit"},
    "B1": {"sw": "mafuriko ya mto", "en": "river flooding"},
    "B2": {"sw": "mafuriko ya ghafla", "en": "flash floods"},
    "B3": {"sw": "hatari ya RVF kwa mifugo", "en": "livestock RVF risk"},
    "B4": {"sw": "ukame", "en": "drought"},
    "C1": {"sw": "hatari ya kipindupindu", "en": "cholera risk"},
    "C2": {"sw": "hatari ya RVF kwa binadamu", "en": "human RVF risk"},
    "C3": {"sw": "sumukuvu kwenye chakula", "en": "food aflatoxin risk"},
    "S1": {"sw": "msimu wa mvua nyingi sana", "en": "a very wet season"},
}

WARD_STATUS_LINES = {
    "ACT": {"sw": "Kata ya {ward}: {phrase}, uhakika {confidence}%.", "en": "{ward} ward: {phrase}, confidence {confidence}%."},
    "WATCH": {"sw": "Kata ya {ward}: huenda {phrase}, uhakika {confidence}%.", "en": "{ward} ward: possible {phrase}, confidence {confidence}%."},
    "ABSTAIN": {"sw": "Kata ya {ward}: hatuna uhakika wa kutosha leo.", "en": "{ward} ward: not certain enough to advise today."},
    "NONE": {"sw": "Kata ya {ward}: hakuna tahadhari leo.", "en": "{ward} ward: no warnings today."},
    "UNREGISTERED": {"sw": "Hujajiandikisha. Chagua 1 kujiandikisha.", "en": "You are not registered. Choose 1 to register."},
}
