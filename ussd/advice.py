"""El Niño advice shown in USSD menu 6 (Ushauri wa El Niño / El Niño advice).

Static, pre-written guidance: no backend or GenAI call, so it stays fast and
works for unregistered numbers. Farm-specific timing comes from menu 3 (ward
status) and the SMS alerts.

Each tip is one screen. Keep every tip short enough that
`test_screens_fit_ussd_limit` passes (about 140 characters). The advice must be
checked by an agronomist, and the Swahili by a native speaker, before the demo.
Topics and tips are matched by position across the two languages.
"""

ELNINO = {
    "sw": {
        "title": "El Niño: mvua kubwa inakuja. Chagua:",
        "topics": [
            ("Kupanda", [
                "Panda mvua zikishaanza vizuri. Tumia mbegu bora zinazokomaa haraka ili uvune kabla ya mvua kubwa zaidi.",
                "Usipande mabondeni au kando ya mito inayofurika. Panda kwenye matuta ili maji yatoke mbali na mizizi.",
                "Chimba mitaro ya kupitisha maji kabla ya kupanda. Weka mbolea kidogo kidogo; mvua kubwa huisomba.",
            ]),
            ("Kuvuna na kuhifadhi", [
                "Mazao yakikomaa, vuna sasa. Usisubiri: mvua juu ya mazao yaliyokomaa huleta uozo na kuvu yenye sumu (aflatoxin).",
                "Kausha nafaka kabisa kabla ya kuhifadhi. Nafaka yenye unyevu huota kuvu yenye sumu kwa watu na mifugo.",
                "Weka magunia juu ya mbao, si sakafuni wala ukutani, chini ya paa lisilovuja. Tumia magunia ya hermetic ukiweza.",
                "Magunia yakilowa, omba mashine ya kukausha: Menyu kuu > 7. Funika magunia na uyaweke juu ya mbao.",
            ]),
            ("Kuvuna maji ya mvua", [
                "Chimba bwawa dogo upande wa chini wa shamba kunasa maji. Tandika plastiki ili maji yadumu hadi kiangazi.",
                "Vuna maji ya paa kwa mifereji hadi kwenye tangi au madumu. Yafunike ili mbu wasizaliane.",
                "Kwenye miteremko chimba mashimo ya zai au matuta. Yanazuia mmomonyoko wa udongo na kuweka unyevu.",
            ]),
            ("Mafuriko na kuhama", [
                "Ukipata tahadhari ya mafuriko, hamisha watu, mifugo, mbegu na vitambulisho mahali pa juu mapema. Fuata maagizo ya kaunti.",
                "Usitembee wala kuendesha gari kwenye maji ya mafuriko yanayotiririka, hata yakionekana machache. Usivuke daraja lililofurika.",
                "Jua mahali pa juu karibu nawe. Weka simu, kitambulisho na mbegu kwenye mfuko usiopitisha maji. Dharura: 999 au 112.",
            ]),
            ("Mifugo", [
                "Hamisha mifugo mahali pa juu kabla ya mafuriko. Usiwalishe karibu na maji yaliyotuama: mbu wa homa ya Bonde la Ufa huzaliana huko.",
                "Chanja mifugo dhidi ya homa ya Bonde la Ufa chanjo ikitolewa. Ripoti mifugo wagonjwa au waliokufa: Menyu kuu > 2 > 2.",
                "Hifadhi nyasi na chakula cha mifugo mahali pakavu na pa juu. Chakula kilicholowa huota kuvu na kuugua mifugo.",
            ]),
            ("Afya ya familia", [
                "Chemsha au tibu maji ya kunywa. Mafuriko husambaza kipindupindu na homa ya matumbo. Nawa mikono kwa sabuni.",
                "Lala ndani ya chandarua chenye dawa. Malaria huongezeka baada ya mvua kubwa.",
                "Usile nyama wala kunywa maziwa mabichi ya mnyama mgonjwa au aliyekufa. Homa ya Bonde la Ufa huambukiza watu hivyo.",
            ]),
        ],
    },
    "en": {
        "title": "El Niño: heavy rain is coming. Choose:",
        "topics": [
            ("Planting", [
                "Plant once the rains are steady. Use certified, early-maturing seed so you harvest before the heaviest rain.",
                "Do not plant in valley bottoms or beside rivers that flood. Plant on ridges so water drains away from roots.",
                "Dig drainage channels before planting. Apply fertiliser in small doses; heavy rain washes it away.",
            ]),
            ("Harvest and storage", [
                "If your crop is mature, harvest now. Rain on a ripe crop causes rot and poisonous mould (aflatoxin).",
                "Dry grain fully before storing. Damp grain grows mould that is poisonous to people and animals.",
                "Keep bags on pallets, off the floor and away from walls, under a roof that does not leak. Use hermetic bags if you can.",
                "If bags get wet, book a dryer: Main menu > 7. Cover the bags and keep them off the ground.",
            ]),
            ("Saving rainwater", [
                "Dig a water pan on the lower side of your plot to catch runoff. Line it with plastic to keep water for the dry months.",
                "Collect roof water with gutters into tanks or drums. Cover them so mosquitoes cannot breed.",
                "On slopes, dig zai pits or terraces. They slow the water, stop soil loss and keep moisture for your crops.",
            ]),
            ("Floods and moving", [
                "If an alert warns of flooding, move people, animals, seed and IDs to higher ground early. Follow county instructions.",
                "Never walk, ride or drive through moving flood water, even if it looks shallow. Do not cross flooded bridges.",
                "Know your nearest high ground. Keep phone, ID and seed in a waterproof bag, ready to go. Emergency: 999 or 112.",
            ]),
            ("Livestock", [
                "Move animals to higher ground before floods. Avoid grazing near standing water: mosquitoes that spread Rift Valley fever breed there.",
                "Vaccinate against Rift Valley fever when vaccine is offered. Report sick or dead animals: Main menu > 2 > 2.",
                "Store hay and feed in a dry, raised place. Wet feed grows mould and makes animals sick.",
            ]),
            ("Family health", [
                "Boil or treat drinking water. Floods spread cholera and typhoid. Wash hands with soap.",
                "Sleep under a treated mosquito net. Malaria rises after heavy rain.",
                "Do not eat meat or drink raw milk from sick or dead animals. Rift Valley fever spreads to people this way.",
            ]),
        ],
    },
}
