# SPDX-License-Identifier: Apache-2.0
"""Voice/locale facts from Gradium's Phonon catalogue, not voice recordings.

Source: https://huggingface.co/gradium/phonon-voices/blob/0ee17205f254b5abc43b266c8a0f215565c352f7/voices.jsonl
Copyright Gradium. See CATALOGUE-LICENSE.txt in the add-on root.
Character voices have language English but no regional locale in the source.
"""

import unicodedata


GROUPS = {
    "en": (
        "Captain Silas Blackwater", "Morwenna of the Deep", "Old Mortlake", "Azgorath",
        "Fennick the Young Elf", "The Thing Under the Stairs", "Vyrmathrax the Ancient",
        "Grub the Ogre", "Master Aldric", "The Snow Queen", "Sir Bramwell", "Fairy Godmother",
    ),
    "de_DE": (
        "Nele", "Jette", "Annika", "Jule", "Lotta", "Josefine", "Anton", "Mirko",
        "Mats", "Henrik", "Leon", "Mika", "Svea",
    ),
    "en_GB": (
        "Maeve", "Freya", "Pippa", "Elodie-Rose", "Imogen", "Lottie", "Alfie",
        "Hugh", "Edwin", "Spencer", "Toby", "Teddy", "Bryony",
    ),
    "en_US": (
        "Zoey", "Juniper", "Everly", "Harper", "Hadley", "Delia", "Easton",
        "Micah", "Trevor", "Russell", "Preston", "Miles",
    ),
    "es_ES": (
        "Noa", "Luc\u00eda-Sol", "Candela", "Celia", "Alba", "Alicia", "Marcos",
        "Borja", "Iker", "Nuno", "Izan", "Guillermo", "Nerea",
    ),
    "es_MX": (
        "Rebeca", "Regina", "Danae", "Azucena", "Jimena", "Ximena", "Lalo",
        "Aldo", "Yael", "Ulises", "Leonel", "Nico",
    ),
    "fr_FR": (
        "No\u00e9mie", "Coralie", "Tiphaine", "Ma\u00eblys", "Orianne", "Sol\u00e8ne",
        "Augustin", "Arnaud", "Jules", "Baptiste", "Lucien", "Marius", "Ninon", "Lilou", "H\u00e9l\u00e8ne",
    ),
    "pt_BR": (
        "Manuela-Lu", "Isadora", "D\u00e9bora", "Rafaela", "Clarice", "Fl\u00e1via",
        "Davi", "Igor", "Kau\u00e3", "Vitor", "Ot\u00e1vio", "Marcelo",
    ),
}


def normalise(name):
    return unicodedata.normalize("NFC", name).casefold()


LANGUAGES = {normalise(name): locale for locale, names in GROUPS.items() for name in names}
